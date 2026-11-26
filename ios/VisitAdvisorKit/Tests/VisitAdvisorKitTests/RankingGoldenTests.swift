import Foundation
import XCTest
@testable import VisitAdvisorKit

private struct Golden: Decodable {
    struct Ranked: Decodable { let id: String; let score: Float }
    struct Case: Decodable {
        let query: [Float]
        let embedding_scores: [String: Float]
        let top3: [Ranked]
    }
    let synthetic: Bool
    let tolerance: Float
    let cases: [Case]
}

final class RankingGoldenTests: XCTestCase {
    private func golden() throws -> Golden {
        try JSONDecoder().decode(Golden.self, from: Data(contentsOf: fixtureURL("ranking-golden.json")))
    }

    /// Accelerate scores + ranker reproduce the Python server arithmetic.
    func testAccelerateMatchesPythonEmbeddingScores() throws {
        let package = try VenuePackage(directory: fixtureURL("synthetic-package"))
        let search = AccelerateExactSearch(matrix: package.matrix)
        let golden = try golden()
        XCTAssertTrue(golden.synthetic)
        for item in golden.cases {
            let query = try normalizedQuery(item.query, dimension: 384)
            let rows = try search.scoresSync(for: query)
            let scores = ExhibitRanker.embeddingScores(rowScores: rows, index: package.index)
            XCTAssertEqual(Set(scores.keys), Set(item.embedding_scores.keys))
            for (id, expected) in item.embedding_scores {
                XCTAssertEqual(scores[id]!.score, expected, accuracy: golden.tolerance, id)
            }
            let ranked = scores.sorted { $0.value.score != $1.value.score
                ? $0.value.score > $1.value.score : $0.key < $1.key }.prefix(3).map(\.key)
            XCTAssertEqual(ranked, item.top3.map(\.id))
        }
    }

    /// GPU arithmetic agrees with the CPU oracle; skipped where Metal is absent.
    func testMetalMatchesAccelerate() async throws {
        let package = try VenuePackage(directory: fixtureURL("synthetic-package"))
        let cpu = AccelerateExactSearch(matrix: package.matrix)
        let gpu: MetalExactSearch
        do { gpu = try MetalExactSearch(matrix: package.matrix) } catch {
            throw XCTSkip("Metal/MPS unavailable on this test host")
        }
        for item in try golden().cases {
            let query = try normalizedQuery(item.query, dimension: 384)
            let expected = try cpu.scoresSync(for: query)
            let actual = try await gpu.scores(for: query)
            XCTAssertEqual(actual.count, expected.count)
            for (a, e) in zip(actual, expected) { XCTAssertEqual(a, e, accuracy: 1e-4) }
        }
    }

    func testInvalidQueriesAreRejected() {
        XCTAssertThrowsError(try normalizedQuery([Float](repeating: 0, count: 384), dimension: 384))
        XCTAssertThrowsError(try normalizedQuery([Float](repeating: 1, count: 383), dimension: 384))
        var nan = [Float](repeating: 1, count: 384); nan[3] = .nan
        XCTAssertThrowsError(try normalizedQuery(nan, dimension: 384))
    }
}
