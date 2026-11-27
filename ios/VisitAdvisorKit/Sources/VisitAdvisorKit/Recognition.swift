import Foundation

/// Normalized box (x0, y0, x1, y1) in frame coordinates, origin top-left.
public struct NormalizedBox: Equatable, Sendable {
    public let x0, y0, x1, y1: Float
    public static let fullFrame = NormalizedBox(x0: 0, y0: 0, x1: 1, y1: 1)

    public init(x0: Float, y0: Float, x1: Float, y1: Float) {
        self.x0 = x0; self.y0 = y0; self.x1 = x1; self.y1 = y1
    }

    public var isValid: Bool {
        [x0, y0, x1, y1].allSatisfy { $0.isFinite && $0 >= 0 && $0 <= 1 } && x0 < x1 && y0 < y1
    }

    var area: Float { (x1 - x0) * (y1 - y0) }
}

public struct ExhibitCandidate: Equatable, Sendable {
    public let exhibitID: String
    public let score: Float
    public let profile: RecognitionProfile
    public let viewIndex: Int
    public let coverageReady: Bool
    public let box: NormalizedBox
}

/// Result of `geometric_evidence` for one planar exhibit (OpenCV bridge).
public struct PlanarEvidence: Equatable, Sendable {
    public let score: Float
    public let viewIndex: Int
    public init(score: Float, viewIndex: Int) {
        self.score = score
        self.viewIndex = viewIndex
    }
}

/// Port of the per-box scoring in `live_camera/pipeline.py::recognize`.
public enum ExhibitRanker {
    public static let globalWeight: Float = 0.75
    public static let planarAcceptance: Float = 0.4

    /// Embedding score per exhibit: view = 0.75·region0 + 0.25·max(regions),
    /// exhibit = max over views. Rows must be in package order.
    public static func embeddingScores(
        rowScores: [Float], index: ReferenceIndex
    ) -> [String: (score: Float, viewIndex: Int)] {
        precondition(rowScores.count == index.rows.count)
        var best: [String: (score: Float, viewIndex: Int)] = [:]
        var start = 0
        while start < index.rows.count {
            let row = index.rows[start]
            var end = start + 1
            while end < index.rows.count,
                  index.rows[end].exhibitID == row.exhibitID,
                  index.rows[end].viewIndex == row.viewIndex { end += 1 }
            let regions = rowScores[start..<end]
            let view = globalWeight * regions[start] + (1 - globalWeight) * regions.max()!
            if let current = best[row.exhibitID], current.score >= view {
                // Keep the earliest view on ties, like Python's max over indices.
            } else {
                best[row.exhibitID] = (view, row.viewIndex)
            }
            start = end
        }
        return best
    }

    /// Candidates for one evaluated box. Planar exhibits never keep an
    /// embedding score: they score only through accepted geometry (else 0),
    /// so a missing verifier fails closed instead of matching on scene context.
    public static func candidates(
        rowScores: [Float], index: ReferenceIndex, box: NormalizedBox,
        planarEvidence: [String: PlanarEvidence]
    ) -> [ExhibitCandidate] {
        embeddingScores(rowScores: rowScores, index: index).compactMap { id, value in
            guard let exhibit = index.exhibits[id] else { return nil }
            var score = value.score
            var view = value.viewIndex
            if exhibit.profile == .planar {
                score = 0
                if let evidence = planarEvidence[id], evidence.score >= planarAcceptance {
                    score = evidence.score
                    view = evidence.viewIndex
                }
            }
            return ExhibitCandidate(
                exhibitID: id, score: max(0, min(1, score)), profile: exhibit.profile,
                viewIndex: view, coverageReady: exhibit.coverageReady, box: box
            )
        }
    }

    /// Best candidate per distinct exhibit across boxes, top three, sorted by
    /// (-score, id) so ties are deterministic and the runner-up is a different exhibit.
    public static func merge(_ boxes: [[ExhibitCandidate]], limit: Int = 3) -> [ExhibitCandidate] {
        var best: [String: ExhibitCandidate] = [:]
        for candidate in boxes.joined() where best[candidate.exhibitID].map({ candidate.score > $0.score }) ?? true {
            best[candidate.exhibitID] = candidate
        }
        return best.values.sorted {
            $0.score != $1.score ? $0.score > $1.score : $0.exhibitID < $1.exhibitID
        }.prefix(limit).map { $0 }
    }
}

public enum FrameDecision: String, Equatable, Sendable {
    case unreadable, deferred, candidate, ambiguous, noMatch = "no_match"
}

/// Server decision rule (`suggestion_allowed`). Uncalibrated packages have no
/// enabled profile, so this can never return `.candidate` for them.
public func decide(
    _ candidates: [ExhibitCandidate], policy: DecisionPolicy, budgetExceeded: Bool
) -> FrameDecision {
    if budgetExceeded { return .deferred }
    guard let first = candidates.first else { return .noMatch }
    let profile = policy.profiles[first.profile.rawValue]
    let floor = profile?.floor ?? policy.candidateFloor
    let margin = profile?.margin ?? policy.candidateMargin
    let second = candidates.count > 1 ? candidates[1].score : 0
    if profile?.enabled == true, first.coverageReady, first.score >= floor, first.score - second >= margin {
        return .candidate
    }
    return first.score >= policy.ambiguousFloor ? .ambiguous : .noMatch
}

/// Port of `Stability` in `static/recognition.mjs` (the visible tentative result).
/// Times are milliseconds on a monotonic clock.
public struct Stability: Sendable {
    public let policy: DecisionPolicy
    private var id: String?
    private var count = 0
    private var lastAt: Double = 0
    private var firstAt: Double = 0
    private var confirmed: String?
    private var lastConfirmedAt: Double = 0
    private var holding = false
    private var box: NormalizedBox?
    private(set) var views: Set<Int> = []

    public init(policy: DecisionPolicy) {
        self.policy = policy
    }

    public mutating func reset() {
        id = nil; count = 0; lastAt = 0; firstAt = 0; confirmed = nil
        lastConfirmedAt = 0; holding = false; box = nil; views = []
    }

    public func current(now: Double) -> String? {
        guard let confirmed, now - lastConfirmedAt <= (holding ? 1500 : 3000) else { return nil }
        return confirmed
    }

    public mutating func observe(_ candidates: [ExhibitCandidate], now: Double, moving: Bool = false) -> String? {
        let first = candidates.first
        let second = candidates.count > 1 ? candidates[1] : nil
        let thresholds = first.flatMap { policy.profiles[$0.profile.rawValue] }
        let strong: Bool = {
            guard let first, first.coverageReady, thresholds?.enabled != false else { return false }
            return first.score >= (thresholds?.floor ?? policy.candidateFloor)
                && first.score - (second?.score ?? 0) >= (thresholds?.margin ?? policy.candidateMargin)
        }()
        if strong, let box, let first, !sameRegion(box, first.box) { reset() }
        if moving || (confirmed != nil && (current(now: now) == nil || (strong && first?.exhibitID != confirmed))) {
            reset()
        }
        if moving { return nil }
        guard strong, let first else {
            holding = true
            if let value = current(now: now) { return value }
            reset()
            return nil
        }
        if id != first.exhibitID || now - lastAt > 3000 {
            count = 0
            firstAt = now
            views = []
        }
        id = first.exhibitID
        lastAt = now
        count += 1
        box = first.box
        views.insert(first.viewIndex)
        holding = false
        if count >= policy.stableFrames, now - firstAt >= Double(policy.minimumStableMs) {
            confirmed = first.exhibitID
            lastConfirmedAt = now
        }
        return confirmed
    }
}

/// Same physical target: IoU ≥ 0.2 or centre distance ≤ 0.15; full frame always matches.
public func sameRegion(_ a: NormalizedBox, _ b: NormalizedBox) -> Bool {
    guard a.isValid, b.isValid else { return false }
    if a.area > 0.95 || b.area > 0.95 { return true }
    let intersection = max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
    let iou = intersection / (a.area + b.area - intersection)
    let dx = (a.x0 + a.x1 - b.x0 - b.x1) / 2
    let dy = (a.y0 + a.y1 - b.y0 - b.y1) / 2
    return iou >= 0.2 || (dx * dx + dy * dy).squareRoot() <= 0.15
}

/// Identity of the session a result belongs to; stale results are discarded.
public struct RecognitionContext: Equatable, Sendable {
    public let generation: Int
    public let venueID: String
    public let packageVersion: String
    public let pipelineContractID: String
    public let trackID: Int

    public init(generation: Int, venueID: String, packageVersion: String, pipelineContractID: String, trackID: Int) {
        self.generation = generation
        self.venueID = venueID
        self.packageVersion = packageVersion
        self.pipelineContractID = pipelineContractID
        self.trackID = trackID
    }
}
