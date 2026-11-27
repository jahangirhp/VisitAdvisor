import Foundation
import VisitAdvisorKit

struct Venue: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let slug: String?
}

enum APIError: LocalizedError {
    case http(Int, String?)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .http(409, "PACKAGE_UNAVAILABLE"): "This venue has no offline package yet."
        case .http(404, _): "Not found on the server."
        case .http(let status, _): "Server error (\(status)). Try again later."
        case .invalidResponse: "The server response was not valid."
        }
    }
}

/// Talks only to the VisitAdvisor backend; never to MongoDB.
struct VenueAPIClient: Sendable {
    let baseURL: URL
    var session: URLSession = .shared

    private func get(_ path: String, headers: [String: String] = [:]) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.timeoutInterval = 30
        headers.forEach { request.setValue($1, forHTTPHeaderField: $0) }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        return (data, http)
    }

    private func check(_ data: Data, _ http: HTTPURLResponse) throws {
        guard (200..<300).contains(http.statusCode) else {
            let code = (try? JSONDecoder().decode([String: String].self, from: data))?["error"]
            throw APIError.http(http.statusCode, code)
        }
    }

    func venues() async throws -> [Venue] {
        let (data, http) = try await get("live/venues")
        try check(data, http)
        return try JSONDecoder().decode([Venue].self, from: data)
    }

    /// Returns nil when the server reports the installed version is current (304).
    func manifest(venueID: String, ifNoneMatch version: String?) async throws -> (VenueManifest, Data)? {
        let headers = version.map { ["If-None-Match": "\"\($0)\""] } ?? [:]
        let (data, http) = try await get("live/venues/\(venueID)/package/manifest", headers: headers)
        if http.statusCode == 304 { return nil }
        try check(data, http)
        return (try JSONDecoder().decode(VenueManifest.self, from: data), data)
    }

    /// Downloads one manifest-listed file to a temporary location.
    func download(venueID: String, version: String, path: String) async throws -> URL {
        let url = baseURL.appending(path: "live/venues/\(venueID)/package/\(version)/\(path)")
        let (location, response) = try await session.download(from: url)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard http.statusCode == 200 else { throw APIError.http(http.statusCode, nil) }
        return location
    }
}
