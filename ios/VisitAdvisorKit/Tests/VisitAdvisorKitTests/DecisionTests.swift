import XCTest
@testable import VisitAdvisorKit

final class DecisionTests: XCTestCase {
    private func index(planar: Bool = false) -> ReferenceIndex {
        // Exhibit A: two views (regions 3 and 1); exhibit B: one view.
        let rows = [
            ReferenceIndex.Row(exhibitID: "A", viewIndex: 0, regionIndex: 0),
            .init(exhibitID: "A", viewIndex: 0, regionIndex: 1),
            .init(exhibitID: "A", viewIndex: 0, regionIndex: 2),
            .init(exhibitID: "A", viewIndex: 1, regionIndex: 0),
            .init(exhibitID: "B", viewIndex: 0, regionIndex: 0),
        ]
        func exhibit(_ id: String, _ profile: RecognitionProfile) -> ReferenceIndex.Exhibit {
            .init(id: id, name: id, type: "x", creator: nil, year: nil, description: nil,
                  profile: profile, coverageReady: true, distinctImages: 2)
        }
        return ReferenceIndex(rows: rows, exhibits: ["A": exhibit("A", planar ? .planar : .multiview),
                                                     "B": exhibit("B", .multiview)])
    }

    private func policy(enabled: Bool) -> DecisionPolicy {
        let profile = DecisionPolicy.Profile(floor: 0.55, margin: 0.04, enabled: enabled)
        return DecisionPolicy(id: "test", calibrated: enabled, candidateFloor: 0.55, candidateMargin: 0.04,
                              stableFrames: 3, minimumStableMs: 600,
                              profiles: enabled ? ["planar": profile, "multiview": profile] : [:])
    }

    func testViewScoreFormulaAndMaxOverViews() {
        let scores = ExhibitRanker.embeddingScores(rowScores: [0.6, 0.9, 0.2, 0.7, 0.5], index: index())
        // view0 = 0.75*0.6 + 0.25*0.9 = 0.675; view1 = 0.7
        XCTAssertEqual(scores["A"]!.score, 0.7, accuracy: 1e-6)
        XCTAssertEqual(scores["A"]!.viewIndex, 1)
        XCTAssertEqual(scores["B"]!.score, 0.5, accuracy: 1e-6)
    }

    func testPlanarExhibitNeedsGeometry() {
        let rows: [Float] = [0.9, 0.9, 0.9, 0.9, 0.5]
        let none = ExhibitRanker.candidates(rowScores: rows, index: index(planar: true), box: .fullFrame, planarEvidence: [:])
        XCTAssertEqual(none.first { $0.exhibitID == "A" }?.score, 0)
        let weak = ExhibitRanker.candidates(rowScores: rows, index: index(planar: true), box: .fullFrame,
                                            planarEvidence: ["A": PlanarEvidence(score: 0.39, viewIndex: 1)])
        XCTAssertEqual(weak.first { $0.exhibitID == "A" }?.score, 0)
        let verified = ExhibitRanker.candidates(rowScores: rows, index: index(planar: true), box: .fullFrame,
                                                planarEvidence: ["A": PlanarEvidence(score: 0.8, viewIndex: 1)])
        XCTAssertEqual(verified.first { $0.exhibitID == "A" }?.score, 0.8)
    }

    func testUncalibratedPolicyNeverConfirmsOnServerRule() {
        let candidates = ExhibitRanker.merge([ExhibitRanker.candidates(
            rowScores: [0.95, 0.95, 0.95, 0.95, 0.1], index: index(), box: .fullFrame, planarEvidence: [:])])
        XCTAssertEqual(decide(candidates, policy: policy(enabled: false), budgetExceeded: false), .ambiguous)
        XCTAssertEqual(decide(candidates, policy: policy(enabled: true), budgetExceeded: false), .candidate)
        XCTAssertEqual(decide(candidates, policy: policy(enabled: true), budgetExceeded: true), .deferred)
        XCTAssertEqual(decide([], policy: policy(enabled: true), budgetExceeded: false), .noMatch)
    }

    func testMarginUsesDistinctExhibits() {
        // A close runner-up exhibit makes the result ambiguous.
        let candidates = ExhibitRanker.merge([ExhibitRanker.candidates(
            rowScores: [0.7, 0.7, 0.7, 0.7, 0.68], index: index(), box: .fullFrame, planarEvidence: [:])])
        XCTAssertEqual(candidates.map(\.exhibitID), ["A", "B"])
        XCTAssertEqual(decide(candidates, policy: policy(enabled: true), budgetExceeded: false), .ambiguous)
    }

    func testStabilityNeedsFramesAndTimeAndResetsOnNewTarget() {
        var stability = Stability(policy: policy(enabled: false))
        let a = ExhibitCandidate(exhibitID: "A", score: 0.8, profile: .multiview, viewIndex: 0,
                                 coverageReady: true, box: .fullFrame)
        XCTAssertNil(stability.observe([a], now: 0))
        XCTAssertNil(stability.observe([a], now: 200))
        XCTAssertNil(stability.observe([a], now: 400))  // 3 frames but < 600 ms
        XCTAssertEqual(stability.observe([a], now: 700), "A")
        // Weak frames hold briefly, then expire.
        XCTAssertEqual(stability.observe([], now: 1500), "A")
        XCTAssertNil(stability.observe([], now: 2300))
        // Motion clears immediately.
        _ = stability.observe([a], now: 3000)
        XCTAssertNil(stability.observe([a], now: 3100, moving: true))
    }

    func testSameRegion() {
        let left = NormalizedBox(x0: 0, y0: 0, x1: 0.3, y1: 0.3)
        let right = NormalizedBox(x0: 0.7, y0: 0.7, x1: 1, y1: 1)
        XCTAssertFalse(sameRegion(left, right))
        XCTAssertTrue(sameRegion(left, .fullFrame))
        XCTAssertTrue(sameRegion(left, NormalizedBox(x0: 0.05, y0: 0.05, x1: 0.32, y1: 0.33)))
    }
}
