import Foundation

/// Mirrors `app/modules/live_camera/venue_package.py`. Field names are the
/// JSON keys written by the exporter; unknown keys are ignored.
public struct VenueManifest: Codable, Equatable, Sendable {
    public static let format = "visitadvisor-venue-package"
    public static let schemaVersion = 1
    /// Must equal the server's `PIPELINE_CONTRACT_ID`. Bump both together.
    public static let pipelineContractID = "object-regions-types-v5/camera-preprocessing-v1"

    public struct Model: Codable, Equatable, Sendable {
        public let role: String
        public let sourceModel: String
        public let serverExportSHA256: String
    }

    public struct EmbeddingSpace: Codable, Equatable, Sendable {
        public let spaceID: String
        public let modelRole: String
        public let dimension: Int
        public let rowCount: Int
        public let dtype: String
        public let byteOrder: String
        public let normalization: String
        public let metric: String
        public let scoreConvention: String
        public let matrixPath: String
    }

    public struct LocalFeatures: Codable, Equatable, Sendable {
        public let pointCount: Int
        public let pointsPath: String
        public let descriptorsPath: String
        public let descriptorDimension: Int
        public let dtype: String
        public let byteOrder: String
    }

    public struct File: Codable, Equatable, Sendable {
        public let path: String
        public let sizeBytes: Int
        public let sha256: String
    }

    public let format: String
    public let schemaVersion: Int
    public let venueID: String
    public let venueName: String
    public let packageVersion: String
    public let catalogRevision: String
    public let createdAt: String
    public let minimumAppVersion: String
    public let pipelineContractID: String
    public let models: [Model]
    public let exhibitCount: Int
    public let viewCount: Int
    public let embeddingSpaces: [EmbeddingSpace]
    public let localFeatures: LocalFeatures
    public let decisionPolicyID: String
    public let automaticIdentificationEnabled: Bool
    public let files: [File]
    public let requiredFiles: [String]

    public var totalBytes: Int { files.reduce(0) { $0 + $1.sizeBytes } }
}

/// `decision-policy.json`. Preview thresholds are uncalibrated unless
/// `calibrated` is true and the matching profile is `enabled`.
public struct DecisionPolicy: Codable, Equatable, Sendable {
    public struct Profile: Codable, Equatable, Sendable {
        public let floor: Float
        public let margin: Float
        public let enabled: Bool
    }

    public let id: String
    public let calibrated: Bool
    public let automaticIdentification: Bool
    public let candidateFloor: Float
    public let candidateMargin: Float
    public let ambiguousFloor: Float
    public let stableFrames: Int
    public let minimumStableMs: Int
    public let objectRegions: Bool
    public let inferenceBudgetMs: Int
    public let profiles: [String: Profile]

    enum CodingKeys: String, CodingKey {
        case id, calibrated, profiles
        case automaticIdentification = "automatic_identification"
        case candidateFloor = "candidate_floor"
        case candidateMargin = "candidate_margin"
        case ambiguousFloor = "ambiguous_floor"
        case stableFrames = "stable_frames"
        case minimumStableMs = "minimum_stable_ms"
        case objectRegions = "object_regions"
        case inferenceBudgetMs = "inference_budget_ms"
    }

    public init(
        id: String, calibrated: Bool, automaticIdentification: Bool = false,
        candidateFloor: Float, candidateMargin: Float, ambiguousFloor: Float = 0.45,
        stableFrames: Int, minimumStableMs: Int, objectRegions: Bool = true,
        inferenceBudgetMs: Int = 1500, profiles: [String: Profile]
    ) {
        self.id = id
        self.calibrated = calibrated
        self.automaticIdentification = automaticIdentification
        self.candidateFloor = candidateFloor
        self.candidateMargin = candidateMargin
        self.ambiguousFloor = ambiguousFloor
        self.stableFrames = stableFrames
        self.minimumStableMs = minimumStableMs
        self.objectRegions = objectRegions
        self.inferenceBudgetMs = inferenceBudgetMs
        self.profiles = profiles
    }
}
