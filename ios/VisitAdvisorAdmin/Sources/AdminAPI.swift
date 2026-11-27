import Foundation

// MARK: - Models (shapes of /api/v1/admin and /live responses)

struct AdminVenue: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let city: String?
}

/// One row of `GET /admin/venues/{id}/objects` (photo coverage per object).
struct AdminObjectRow: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let type: String
    let target: Int
    let prepared: Int
    let pending: Int
    let missing: Int
    let needsPhotos: Bool
    let firstPhotoID: String?

    enum CodingKeys: String, CodingKey {
        case id, name, type, target, prepared, pending, missing
        case needsPhotos = "needs_photos"
        case firstPhotoID = "first_photo_id"
    }
}

struct AdminInventory: Codable {
    let objects: [AdminObjectRow]
}

struct AdminPhoto: Codable, Identifiable, Hashable {
    struct Mask: Codable, Hashable { let sha256: String? }
    struct Capture: Codable, Hashable {
        struct Selection: Codable, Hashable { let method: String }
        let requestedView: String?
        let selection: Selection?
        enum CodingKeys: String, CodingKey {
            case requestedView = "requested_view"
            case selection
        }
    }

    let id: String
    let viewpoint: String?
    let sha256: String?
    let foregroundMask: Mask?
    let captureMetadata: Capture?

    enum CodingKeys: String, CodingKey {
        case id = "_id"
        case viewpoint, sha256
        case foregroundMask = "foreground_mask"
        case captureMetadata = "capture_metadata"
    }

    var hasMask: Bool { foregroundMask != nil }

    /// How recognition uses this photo (see reference_focus.py).
    var preparationLabel: String {
        if hasMask { return "Background removed" }
        if let method = captureMetadata?.selection?.method, method != "full-frame" { return "Box crop" }
        return "Full photo (background kept)"
    }
}

struct AdminObjectDetail: Codable {
    struct Source: Codable, Hashable { let id: String; let title: String }
    let id: String
    let name: String
    let type: String
    let target: Int
    let prepared: Int
    let pending: Int
    let missing: Int
    let stale: Bool
    let sources: [Source]
    let photos: [AdminPhoto]
}

struct CreatedObject: Codable {
    let id: String
    let name: String
    let type: String
    let sourceIDs: [String]
    enum CodingKeys: String, CodingKey {
        case id, name, type
        case sourceIDs = "source_ids"
    }
}

/// `GET /live/bundle/catalog.json?location_id=` — what the visitor camera matches against.
struct LiveCatalog: Codable {
    struct Object: Codable, Hashable { let id: String; let name: String; let type: String }
    struct Policy: Codable {
        let candidateFloor: Double?
        let candidateMargin: Double?
        let frameSize: Int?
        enum CodingKeys: String, CodingKey {
            case candidateFloor = "candidate_floor"
            case candidateMargin = "candidate_margin"
            case frameSize = "frame_size"
        }
    }
    let referenceVersion: String
    let policy: Policy
    let objects: [Object]
    enum CodingKeys: String, CodingKey {
        case referenceVersion = "reference_version"
        case policy, objects
    }
}

/// `POST /live/venues/{id}/package/prepare` (admin token).
struct PreparedPackage: Codable {
    let packageVersion: String
    let exhibitCount: Int
    let viewCount: Int
    let totalBytes: Int
    let createdAt: String
    enum CodingKeys: String, CodingKey {
        case packageVersion = "package_version"
        case exhibitCount = "exhibit_count"
        case viewCount = "view_count"
        case totalBytes = "total_bytes"
        case createdAt = "created_at"
    }
}

struct LiveResult: Codable {
    struct Candidate: Codable, Hashable { let id: String; let score: Double }
    let candidates: [Candidate]
    let inferenceMs: Double?
}

// MARK: - Client

enum AdminError: LocalizedError {
    case server(code: String, message: String, status: Int)
    case badResponse

    var errorDescription: String? {
        switch self {
        case .server(_, let message, _): message
        case .badResponse: "The server returned an unexpected response."
        }
    }

    var code: String? { if case .server(let code, _, _) = self { code } else { nil } }
    var status: Int? { if case .server(_, _, let status) = self { status } else { nil } }
}

/// Admin API client. The token stays in the Keychain and is only sent as a
/// Bearer header to the configured VisitAdvisor server.
struct AdminAPI: Sendable {
    let baseURL: URL
    let token: String

    static var configuredBaseURL: URL {
        (Bundle.main.object(forInfoDictionaryKey: "VisitAdvisorAPIBaseURL") as? String)
            .flatMap(URL.init(string:)) ?? URL(string: "https://visitadvisor-api-t4ckjib5aq-ez.a.run.app")!
    }

    private func adminURL(_ path: String) -> URL { baseURL.appending(path: "api/v1/admin/" + path) }

    private func send(_ request: URLRequest) async throws -> Data {
        var request = request
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw AdminError.badResponse }
        guard (200..<300).contains(http.statusCode) else { throw Self.error(data, status: http.statusCode) }
        return data
    }

    static func error(_ data: Data, status: Int) -> AdminError {
        struct Envelope: Decodable {
            struct Body: Decodable { let code: String; let message: String }
            let error: Body?
        }
        struct Flat: Decodable { let error: String; let reason: String? }
        if let envelope = try? JSONDecoder().decode(Envelope.self, from: data), let body = envelope.error {
            return .server(code: body.code, message: body.message, status: status)
        }
        if let flat = try? JSONDecoder().decode(Flat.self, from: data) {
            let readable = (flat.reason ?? flat.error).replacingOccurrences(of: "_", with: " ").capitalized
            return .server(code: flat.error, message: readable, status: status)
        }
        return .server(code: "HTTP_\(status)", message: "Server error \(status).", status: status)
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await send(URLRequest(url: adminURL(path))))
    }

    // Venues & objects

    func venues() async throws -> [AdminVenue] { try await get("venues") }

    func objects(venue: String) async throws -> [AdminObjectRow] {
        let inventory: AdminInventory = try await get("venues/\(venue)/objects")
        return inventory.objects
    }

    func detail(venue: String, object: String) async throws -> AdminObjectDetail {
        try await get("venues/\(venue)/objects/\(object)")
    }

    func createObject(venue: String, name: String, type: String, year: String?, description: String?) async throws -> CreatedObject {
        var body: [String: String] = ["name": name, "type": type]
        if let year, !year.isEmpty { body["year"] = year }
        if let description, !description.isEmpty { body["description"] = description }
        var request = URLRequest(url: adminURL("venues/\(venue)/objects"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        return try JSONDecoder().decode(CreatedObject.self, from: try await send(request))
    }

    func deleteObject(venue: String, object: String) async throws {
        var request = URLRequest(url: adminURL("venues/\(venue)/objects/\(object)"))
        request.httpMethod = "DELETE"
        _ = try await send(request)
    }

    func deletePhoto(venue: String, object: String, photo: String) async throws {
        var request = URLRequest(url: adminURL("venues/\(venue)/objects/\(object)/photos/\(photo)"))
        request.httpMethod = "DELETE"
        _ = try await send(request)
    }

    func photoData(venue: String, object: String, photo: String, mask: Bool = false) async throws -> Data {
        try await send(URLRequest(url: adminURL("venues/\(venue)/objects/\(object)/photos/\(photo)" + (mask ? "/mask" : ""))))
    }

    /// Uploads one original photo (JPEG) with its capture details and optional mask.
    func upload(venue: String, object: String, photo: CapturedPhoto, captureGroup: String, sourceID: String?) async throws {
        var form = MultipartForm()
        form.add(field: "viewpoint", photo.view.label)
        form.add(field: "capture_group", captureGroup)
        form.add(field: "rights", "Photo rights details not supplied.")
        form.add(field: "source_id", sourceID ?? "")
        form.add(field: "capture_metadata", String(decoding: try photo.metadataJSON(), as: UTF8.self))
        form.add(file: "image", filename: "photo.jpg", type: "image/jpeg", data: photo.jpeg)
        if photo.useMask, let mask = photo.maskPNG {
            form.add(file: "foreground_mask", filename: "foreground-mask.png", type: "image/png", data: mask)
        }
        var request = URLRequest(url: adminURL("venues/\(venue)/objects/\(object)/photos"))
        request.httpMethod = "POST"
        request.setValue(form.contentType, forHTTPHeaderField: "Content-Type")
        request.httpBody = form.finished
        _ = try await send(request)
    }

    /// Prepares fingerprints for newly uploaded photos ("Save" in the web admin).
    func save(venue: String, object: String) async throws {
        var request = URLRequest(url: adminURL("venues/\(venue)/objects/\(object)/save"))
        request.httpMethod = "POST"
        request.timeoutInterval = 180
        _ = try await send(request)
    }

    // Visitor camera endpoints (public, same as the website)

    func liveCatalog(venue: String) async throws -> LiveCatalog {
        var components = URLComponents(url: baseURL.appending(path: "live/bundle/catalog.json"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "location_id", value: venue)]
        let (data, response) = try await URLSession.shared.data(from: components.url!)
        guard let http = response as? HTTPURLResponse else { throw AdminError.badResponse }
        guard http.statusCode == 200 else { throw Self.error(data, status: http.statusCode) }
        return try JSONDecoder().decode(LiveCatalog.self, from: data)
    }

    /// Asks the server to build the venue's offline package now.
    func preparePackage(venue: String) async throws -> PreparedPackage {
        var request = URLRequest(url: baseURL.appending(path: "live/venues/\(venue)/package/prepare"))
        request.httpMethod = "POST"
        request.timeoutInterval = 180
        do {
            return try JSONDecoder().decode(PreparedPackage.self, from: try await send(request))
        } catch let failure as AdminError {
            throw Self.packageError(failure)
        }
    }

    private static func packageError(_ failure: AdminError) -> AdminError {
        let message: String
        switch failure.code {
        case "OFFLINE_PACKAGES_DISABLED":
            message = "Offline downloads are switched off on the server (LIVE_CAMERA_OFFLINE_PACKAGES)."
        case "PACKAGE_UNAVAILABLE":
            message = "The server could not build this venue's package (\(failure.errorDescription ?? "unknown reason"))."
        case "READ_ONLY", "HTTP_404", "NOT_FOUND":
            message = "The server does not have this feature yet. Deploy the latest server code first."
        case "REFERENCES_EXPIRED", "REFERENCES_CHANGED":
            message = "The venue's references just changed. Try again in a moment."
        default:
            return failure
        }
        return .server(code: failure.code ?? "", message: message, status: failure.status ?? 0)
    }

    func recognize(venue: String, referenceVersion: String, jpeg: Data) async throws -> LiveResult {
        var components = URLComponents(url: baseURL.appending(path: "live/recognize"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "location_id", value: venue),
                                 URLQueryItem(name: "reference_version", value: referenceVersion)]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        request.httpBody = jpeg
        request.timeoutInterval = 8
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw AdminError.badResponse }
        guard http.statusCode == 200 else { throw Self.error(data, status: http.statusCode) }
        return try JSONDecoder().decode(LiveResult.self, from: data)
    }
}

struct MultipartForm {
    let boundary = "VisitAdvisor-\(UUID().uuidString)"
    private(set) var body = Data()
    var contentType: String { "multipart/form-data; boundary=\(boundary)" }

    mutating func add(field name: String, _ value: String) {
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
    }

    mutating func add(file name: String, filename: String, type: String, data: Data) {
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\nContent-Type: \(type)\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n".utf8))
    }

    var finished: Data { body + Data("--\(boundary)--\r\n".utf8) }
}
