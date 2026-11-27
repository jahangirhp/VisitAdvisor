import Foundation
import VisitAdvisorKit

@MainActor
final class LibraryModel: ObservableObject {
    enum DownloadState: Equatable {
        case notInstalled, downloading(Double), installed(version: String, bytes: Int), failed(String)
    }

    @Published private(set) var venues: [Venue] = []
    @Published private(set) var downloads: [String: DownloadState] = [:]
    @Published var error: String?

    let client: VenueAPIClient
    let store: VenuePackageStore?
    private var tasks: [String: Task<Void, Never>] = [:]

    init() {
        let base = (Bundle.main.object(forInfoDictionaryKey: "VisitAdvisorAPIBaseURL") as? String)
            .flatMap(URL.init(string:)) ?? URL(string: "https://visitadvisor-api-t4ckjib5aq-ez.a.run.app")!
        client = VenueAPIClient(baseURL: base)
        store = try? VenuePackageStore()
    }

    private var cacheURL: URL? {
        try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            .appending(path: "venues.json")
    }

    func refresh() async {
        do {
            venues = try await client.venues()
            if let cacheURL { try? JSONEncoder().encode(venues).write(to: cacheURL, options: .atomic) }
            error = nil
        } catch {
            // Offline: show the last known list so installed venues stay usable.
            if venues.isEmpty, let cacheURL, let data = try? Data(contentsOf: cacheURL) {
                venues = (try? JSONDecoder().decode([Venue].self, from: data)) ?? []
            }
            self.error = "You're offline. Downloaded venues still work."
        }
        guard let store else { return }
        for venue in venues {
            if let version = await store.activeVersion(venueID: venue.id) {
                downloads[venue.id] = .installed(version: version, bytes: await store.installedSize(venueID: venue.id) ?? 0)
            } else if downloads[venue.id] == nil {
                downloads[venue.id] = .notInstalled
            }
        }
    }

    func download(_ venue: Venue) {
        guard let store, tasks[venue.id] == nil else { return }
        downloads[venue.id] = .downloading(0)
        tasks[venue.id] = Task {
            do {
                let package = try await store.install(venueID: venue.id, client: client) { progress in
                    Task { @MainActor in
                        self.downloads[venue.id] = .downloading(
                            Double(progress.completedBytes) / Double(max(progress.totalBytes, 1)))
                    }
                }
                if let package {
                    downloads[venue.id] = .installed(version: package.manifest.packageVersion, bytes: package.manifest.totalBytes)
                    await store.release(package)
                }
            } catch is CancellationError {
                downloads[venue.id] = await store.activeVersion(venueID: venue.id) == nil ? .notInstalled : downloads[venue.id]
            } catch {
                downloads[venue.id] = .failed(error.localizedDescription)
            }
            tasks[venue.id] = nil
        }
    }

    func cancel(_ venue: Venue) {
        tasks[venue.id]?.cancel()
    }

    func delete(_ venue: Venue) async {
        do {
            try await store?.delete(venueID: venue.id)
            downloads[venue.id] = .notInstalled
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Opens the installed package (full checksum validation) for scanning.
    func openPackage(_ venue: Venue) async -> VenuePackage? {
        do {
            return try await store?.openActive(venueID: venue.id)
        } catch {
            downloads[venue.id] = .failed("The offline package is damaged. Download it again.")
            return nil
        }
    }
}
