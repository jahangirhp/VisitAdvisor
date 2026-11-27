import Foundation
import SQLite3
import VisitAdvisorKit

struct ExhibitContent: Identifiable, Sendable {
    struct Source: Sendable, Hashable { let title: String; let url: URL? }
    let id: String
    let name: String
    let type: String
    let creator: String?
    let year: String?
    let description: String?
    let sources: [Source]
}

/// Offline exhibit content from the installed package's catalog.sqlite.
enum CatalogContent {
    static func exhibit(_ id: String, in package: VenuePackage) -> ExhibitContent? {
        guard let exhibit = package.index.exhibits[id] else { return nil }
        var sources: [ExhibitContent.Source] = []
        var db: OpaquePointer?
        let path = package.directory.appending(path: "catalog.sqlite").path
        if sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK, let db {
            defer { sqlite3_close(db) }
            var statement: OpaquePointer?
            let sql = "SELECT title, url FROM exhibit_sources WHERE exhibit_id = ? ORDER BY position"
            if sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement {
                defer { sqlite3_finalize(statement) }
                sqlite3_bind_text(statement, 1, id, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
                while sqlite3_step(statement) == SQLITE_ROW {
                    let title = sqlite3_column_text(statement, 0).map { String(cString: $0) } ?? ""
                    let url = sqlite3_column_text(statement, 1).flatMap { URL(string: String(cString: $0)) }
                    sources.append(.init(title: title, url: url))
                }
            }
        }
        return ExhibitContent(
            id: id, name: exhibit.name, type: exhibit.type, creator: exhibit.creator,
            year: exhibit.year, description: exhibit.description, sources: sources)
    }
}
