import CryptoKit
import Foundation
import SQLite3

public enum VenuePackageError: Error, Equatable, Sendable {
    case incompatible(String)
    case placeholder
    case unsafePath
    case missingFile
    case sizeMismatch
    case checksumMismatch
    case tooLarge
    case invalidMatrix(String)
    case invalidMapping(String)
    case sqlite(String)
}

/// A fully validated, immutable package opened read-only. Recognition holds one
/// instance for its whole lifetime, so a later install can never mix matrices,
/// mappings or policies inside a single prediction.
public final class VenuePackage: Sendable {
    public let directory: URL
    public let manifest: VenueManifest
    public let policy: DecisionPolicy
    public let matrix: ReferenceMatrix
    public let index: ReferenceIndex

    public static let maximumBytes = 512 * 1024 * 1024
    static let placeholderMarkers = ["EXAMPLE_", "REPLACE_WITH", "UNCONFIGURED"]

    /// Validates every rule the Python `validate_package` enforces.
    public init(directory: URL, verifyChecksums: Bool = true) throws {
        let root = directory.standardizedFileURL
        let manifestData = try Data(contentsOf: root.appendingPathComponent("manifest.json"))
        let manifest = try JSONDecoder().decode(VenueManifest.self, from: manifestData)
        guard manifest.format == VenueManifest.format,
              manifest.schemaVersion == VenueManifest.schemaVersion,
              manifest.pipelineContractID == VenueManifest.pipelineContractID,
              !manifest.automaticIdentificationEnabled
        else { throw VenuePackageError.incompatible("manifest") }
        let text = String(decoding: manifestData, as: UTF8.self)
        if Self.placeholderMarkers.contains(where: text.contains) {
            throw VenuePackageError.placeholder
        }
        guard manifest.totalBytes <= Self.maximumBytes else { throw VenuePackageError.tooLarge }
        let listed = Set(manifest.files.map(\.path))
        guard Set(manifest.requiredFiles).isSubset(of: listed) else { throw VenuePackageError.missingFile }

        for file in manifest.files {
            let url = try Self.resolve(file.path, in: root)
            let attributes = try? FileManager.default.attributesOfItem(atPath: url.path)
            guard let size = attributes?[.size] as? NSNumber else { throw VenuePackageError.missingFile }
            guard size.intValue == file.sizeBytes else { throw VenuePackageError.sizeMismatch }
            if verifyChecksums, try Self.sha256(of: url) != file.sha256 {
                throw VenuePackageError.checksumMismatch
            }
        }

        guard manifest.embeddingSpaces.count == 1, let space = manifest.embeddingSpaces.first,
              space.dimension == 384, space.dtype == "float32", space.byteOrder == "little",
              space.normalization == "l2", space.metric == "dot", space.rowCount > 0
        else { throw VenuePackageError.incompatible("embedding space") }
        let matrixURL = try Self.resolve(space.matrixPath, in: root)
        let matrix = try ReferenceMatrix(url: matrixURL, rows: space.rowCount, columns: space.dimension)

        let policyURL = try Self.resolve("decision-policy.json", in: root)
        let policy = try JSONDecoder().decode(DecisionPolicy.self, from: Data(contentsOf: policyURL))
        guard !policy.automaticIdentification, policy.stableFrames >= 3 else {
            throw VenuePackageError.incompatible("decision policy")
        }

        let index = try ReferenceIndex(
            url: Self.resolve("catalog.sqlite", in: root),
            spaceID: space.spaceID,
            rowCount: space.rowCount,
            exhibitCount: manifest.exhibitCount,
            localPointCount: manifest.localFeatures.pointCount
        )
        self.directory = root
        self.manifest = manifest
        self.policy = policy
        self.matrix = matrix
        self.index = index
    }

    /// Rejects absolute paths, `..`, backslashes and anything escaping `root`.
    static func resolve(_ relative: String, in root: URL) throws -> URL {
        guard !relative.isEmpty, !relative.hasPrefix("/"), !relative.contains("\\"),
              !relative.split(separator: "/").contains("..")
        else { throw VenuePackageError.unsafePath }
        let url = root.appendingPathComponent(relative).standardizedFileURL
        guard url.path.hasPrefix(root.path + "/") else { throw VenuePackageError.unsafePath }
        return url
    }

    static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

/// Contiguous row-major float32 matrix, validated finite and L2-normalized.
public struct ReferenceMatrix: Sendable {
    public let rows: Int
    public let columns: Int
    public let values: [Float]

    public init(url: URL, rows: Int, columns: Int) throws {
        let (count, overflow) = rows.multipliedReportingOverflow(by: columns)
        let (bytes, overflow2) = count.multipliedReportingOverflow(by: 4)
        guard !overflow, !overflow2, rows > 0, columns > 0 else {
            throw VenuePackageError.invalidMatrix("shape")
        }
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        guard data.count == bytes else { throw VenuePackageError.invalidMatrix("size") }
        var values = [Float](repeating: 0, count: count)
        _ = values.withUnsafeMutableBytes { data.copyBytes(to: $0) }
        // Files are little-endian; convert only on a big-endian host.
        if UInt32(littleEndian: 1) != 1 {
            values = values.map { Float(bitPattern: UInt32(littleEndian: $0.bitPattern)) }
        }
        for row in 0..<rows {
            var norm: Float = 0
            for column in 0..<columns {
                let value = values[row * columns + column]
                guard value.isFinite else { throw VenuePackageError.invalidMatrix("non-finite") }
                norm += value * value
            }
            guard norm > 0 else { throw VenuePackageError.invalidMatrix("zero norm") }
            guard abs(norm.squareRoot() - 1) < 1e-3 else {
                throw VenuePackageError.invalidMatrix("not normalized")
            }
        }
        self.rows = rows
        self.columns = columns
        self.values = values
    }

    public init(rows: Int, columns: Int, values: [Float]) {
        precondition(values.count == rows * columns)
        self.rows = rows
        self.columns = columns
        self.values = values
    }
}

/// Row -> (exhibit, view, region) mapping and exhibit metadata from catalog.sqlite.
public struct ReferenceIndex: Sendable {
    public struct Row: Equatable, Sendable {
        public let exhibitID: String
        public let viewIndex: Int
        public let regionIndex: Int
    }

    public struct Exhibit: Equatable, Sendable {
        public let id: String
        public let name: String
        public let type: String
        public let creator: String?
        public let year: String?
        public let description: String?
        public let profile: RecognitionProfile
        public let coverageReady: Bool
        public let distinctImages: Int
    }

    /// One planar reference view's slice of the SIFT point/descriptor files.
    public struct LocalView: Equatable, Sendable {
        public let exhibitID: String
        public let viewIndex: Int
        public let firstPoint: Int
        public let count: Int
        public let width: Double
        public let height: Double
    }

    public let rows: [Row]
    public let exhibits: [String: Exhibit]
    public let localViews: [LocalView]

    public init(rows: [Row], exhibits: [String: Exhibit], localViews: [LocalView] = []) {
        self.rows = rows
        self.exhibits = exhibits
        self.localViews = localViews
    }

    init(url: URL, spaceID: String, rowCount: Int, exhibitCount: Int, localPointCount: Int) throws {
        var db: OpaquePointer?
        guard sqlite3_open_v2(url.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK, let db else {
            throw VenuePackageError.sqlite("open")
        }
        defer { sqlite3_close(db) }
        guard try Self.scalar(db, "PRAGMA integrity_check") == "ok" else {
            throw VenuePackageError.sqlite("integrity")
        }

        var rows: [Row] = []
        try Self.query(
            db,
            "SELECT row_index, exhibit_id, view_index, region_index FROM vector_rows "
                + "WHERE space_id = ? ORDER BY row_index",
            bind: [spaceID]
        ) { statement in
            guard Int(sqlite3_column_int64(statement, 0)) == rows.count else {
                throw VenuePackageError.invalidMapping("row order")
            }
            rows.append(Row(
                exhibitID: Self.text(statement, 1) ?? "",
                viewIndex: Int(sqlite3_column_int64(statement, 2)),
                regionIndex: Int(sqlite3_column_int64(statement, 3))
            ))
        }
        guard rows.count == rowCount else { throw VenuePackageError.invalidMapping("row coverage") }

        var exhibits: [String: Exhibit] = [:]
        try Self.query(
            db,
            "SELECT id, name, type, creator, year, description, profile, coverage_ready, "
                + "distinct_images FROM exhibits"
        ) { statement in
            guard let id = Self.text(statement, 0),
                  let profile = RecognitionProfile(rawValue: Self.text(statement, 6) ?? "")
            else { throw VenuePackageError.invalidMapping("exhibit") }
            exhibits[id] = Exhibit(
                id: id,
                name: Self.text(statement, 1) ?? "",
                type: Self.text(statement, 2) ?? "other",
                creator: Self.text(statement, 3),
                year: Self.text(statement, 4),
                description: Self.text(statement, 5),
                profile: profile,
                coverageReady: sqlite3_column_int64(statement, 7) != 0,
                distinctImages: Int(sqlite3_column_int64(statement, 8))
            )
        }
        guard exhibits.count == exhibitCount else { throw VenuePackageError.invalidMapping("exhibit count") }
        guard rows.allSatisfy({ exhibits[$0.exhibitID] != nil }),
              Set(rows.map(\.exhibitID)) == Set(exhibits.keys)
        else { throw VenuePackageError.invalidMapping("unresolved row") }
        // Every view row must start with region 0 (the full-image vector).
        var previous: (String, Int)?
        for row in rows where previous.map({ $0 != (row.exhibitID, row.viewIndex) }) ?? true {
            guard row.regionIndex == 0 else { throw VenuePackageError.invalidMapping("region order") }
            previous = (row.exhibitID, row.viewIndex)
        }

        var points = 0
        var localViews: [LocalView] = []
        try Self.query(
            db,
            "SELECT exhibit_id, view_index, first_point, point_count, width, height "
                + "FROM local_features ORDER BY first_point"
        ) {
            let view = LocalView(
                exhibitID: Self.text($0, 0) ?? "",
                viewIndex: Int(sqlite3_column_int64($0, 1)),
                firstPoint: Int(sqlite3_column_int64($0, 2)),
                count: Int(sqlite3_column_int64($0, 3)),
                width: sqlite3_column_double($0, 4),
                height: sqlite3_column_double($0, 5)
            )
            guard view.firstPoint == points, (0...800).contains(view.count), exhibits[view.exhibitID] != nil else {
                throw VenuePackageError.invalidMapping("local features")
            }
            points += view.count
            localViews.append(view)
        }
        guard points == localPointCount else { throw VenuePackageError.invalidMapping("local features") }
        self.rows = rows
        self.exhibits = exhibits
        self.localViews = localViews
    }

    private static func text(_ statement: OpaquePointer, _ column: Int32) -> String? {
        guard let value = sqlite3_column_text(statement, column) else { return nil }
        return String(cString: value)
    }

    private static func scalar(_ db: OpaquePointer, _ sql: String) throws -> String? {
        var result: String?
        try query(db, sql) { result = text($0, 0) }
        return result
    }

    private static func query(
        _ db: OpaquePointer, _ sql: String, bind: [String] = [],
        row: (OpaquePointer) throws -> Void
    ) throws {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw VenuePackageError.sqlite("prepare")
        }
        defer { sqlite3_finalize(statement) }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        for (position, value) in bind.enumerated() {
            sqlite3_bind_text(statement, Int32(position + 1), value, -1, transient)
        }
        while true {
            switch sqlite3_step(statement) {
            case SQLITE_ROW: try row(statement)
            case SQLITE_DONE: return
            default: throw VenuePackageError.sqlite("step")
            }
        }
    }
}

public enum RecognitionProfile: String, Sendable {
    case planar
    case multiview
}
