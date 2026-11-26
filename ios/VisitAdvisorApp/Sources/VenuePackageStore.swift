import Foundation
import VisitAdvisorKit

/// Installs immutable venue packages under Application Support (never Caches/tmp,
/// which the system may purge), excluded from iCloud backup.
///
/// Layout: <root>/<venueID>/<version>/...  plus <root>/<venueID>/active (pointer).
/// Activation replaces the pointer atomically; the previous version is kept
/// until the new one has loaded, and deleted only when no recognizer uses it.
actor VenuePackageStore {
    enum StoreError: LocalizedError {
        case insufficientSpace(needed: Int64)
        case incompatible(String)

        var errorDescription: String? {
            switch self {
            case .insufficientSpace(let needed):
                "Not enough free space. Free about \(ByteCountFormatter.string(fromByteCount: needed, countStyle: .file)) and try again."
            case .incompatible(let reason): "This package is not compatible with this app version (\(reason))."
            }
        }
    }

    struct Progress: Sendable { let completedBytes: Int; let totalBytes: Int }

    private let root: URL
    private let fileManager = FileManager.default
    private var inUse: [URL: Int] = [:]

    init() throws {
        let support = try FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        var root = support.appending(path: "VenuePackages", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try root.setResourceValues(values)
        self.root = root
        // Recover from interrupted installs: staging dirs are never active.
        for venue in (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? [] {
            for entry in (try? FileManager.default.contentsOfDirectory(at: venue, includingPropertiesForKeys: nil)) ?? []
            where entry.lastPathComponent.hasPrefix(".staging-") {
                try? FileManager.default.removeItem(at: entry)
            }
        }
    }

    private func venueDirectory(_ venueID: String) -> URL { root.appending(path: venueID, directoryHint: .isDirectory) }

    func activeVersion(venueID: String) -> String? {
        try? String(contentsOf: venueDirectory(venueID).appending(path: "active"), encoding: .utf8)
    }

    /// Opens and fully validates the active package (checksums included).
    func openActive(venueID: String) throws -> VenuePackage? {
        guard let version = activeVersion(venueID: venueID) else { return nil }
        let directory = venueDirectory(venueID).appending(path: version, directoryHint: .isDirectory)
        let package = try VenuePackage(directory: directory)
        inUse[package.directory, default: 0] += 1
        return package
    }

    func release(_ package: VenuePackage) {
        inUse[package.directory, default: 1] -= 1
        if inUse[package.directory] == 0 { inUse[package.directory] = nil }
    }

    func install(
        venueID: String, client: VenueAPIClient,
        progress: @Sendable (Progress) -> Void
    ) async throws -> VenuePackage? {
        let current = activeVersion(venueID: venueID)
        guard let fetched = try await client.manifest(venueID: venueID, ifNoneMatch: current) else {
            return try openActive(venueID: venueID)  // already current
        }
        let (manifest, manifestData) = fetched
        guard manifest.pipelineContractID == VenueManifest.pipelineContractID,
              manifest.schemaVersion == VenueManifest.schemaVersion,
              manifest.venueID == venueID
        else { throw StoreError.incompatible("pipeline \(manifest.pipelineContractID)") }
        guard manifest.totalBytes <= VenuePackage.maximumBytes else { throw StoreError.incompatible("size") }

        // Staging + installed copy (+ previous version kept until activation).
        let needed = Int64(manifest.totalBytes) * 2
        let available = (try? root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage ?? .max
        guard available > needed else { throw StoreError.insufficientSpace(needed: needed) }

        let venue = venueDirectory(venueID)
        try fileManager.createDirectory(at: venue, withIntermediateDirectories: true)
        let staging = venue.appending(path: ".staging-\(UUID().uuidString)", directoryHint: .isDirectory)
        try fileManager.createDirectory(at: staging, withIntermediateDirectories: true)
        do {
            var completed = 0
            for file in manifest.files {
                try Task.checkCancellation()
                let destination = try stagedURL(file.path, in: staging)
                try fileManager.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
                let temporary = try await client.download(venueID: venueID, version: manifest.packageVersion, path: file.path)
                try fileManager.moveItem(at: temporary, to: destination)
                completed += file.sizeBytes
                progress(Progress(completedBytes: completed, totalBytes: manifest.totalBytes))
            }
            try manifestData.write(to: staging.appending(path: "manifest.json"))
            // Full validation (sizes, SHA-256, matrix, mapping) before activation.
            _ = try VenuePackage(directory: staging)
            let target = venue.appending(path: manifest.packageVersion, directoryHint: .isDirectory)
            if fileManager.fileExists(atPath: target.path) { try fileManager.removeItem(at: target) }
            try fileManager.moveItem(at: staging, to: target)
            try Data(manifest.packageVersion.utf8).write(to: venue.appending(path: "active"), options: .atomic)
        } catch {
            try? fileManager.removeItem(at: staging)
            throw error  // the previous active version is untouched
        }
        let package = try openActive(venueID: venueID)
        pruneOldVersions(venueID: venueID)
        return package
    }

    /// Deletes inactive versions that no recognizer currently holds.
    func pruneOldVersions(venueID: String) {
        guard let active = activeVersion(venueID: venueID) else { return }
        let venue = venueDirectory(venueID)
        for entry in (try? fileManager.contentsOfDirectory(at: venue, includingPropertiesForKeys: nil)) ?? [] {
            let name = entry.lastPathComponent
            guard name != active, name != "active", !name.hasPrefix(".") else { continue }
            if inUse[entry.standardizedFileURL] == nil { try? fileManager.removeItem(at: entry) }
        }
    }

    func delete(venueID: String) throws {
        let venue = venueDirectory(venueID)
        guard let active = activeVersion(venueID: venueID) else { return }
        let directory = venue.appending(path: active, directoryHint: .isDirectory).standardizedFileURL
        guard inUse[directory] == nil else { throw StoreError.incompatible("package is in use") }
        try fileManager.removeItem(at: venue)
    }

    func installedSize(venueID: String) -> Int? {
        guard let version = activeVersion(venueID: venueID),
              let data = try? Data(contentsOf: venueDirectory(venueID).appending(path: "\(version)/manifest.json")),
              let manifest = try? JSONDecoder().decode(VenueManifest.self, from: data)
        else { return nil }
        return manifest.totalBytes
    }

    private func stagedURL(_ relative: String, in staging: URL) throws -> URL {
        guard !relative.isEmpty, !relative.hasPrefix("/"), !relative.contains("\\"),
              !relative.split(separator: "/").contains("..")
        else { throw VenuePackageError.unsafePath }
        let url = staging.appending(path: relative).standardizedFileURL
        guard url.path.hasPrefix(staging.standardizedFileURL.path + "/") else { throw VenuePackageError.unsafePath }
        return url
    }
}
