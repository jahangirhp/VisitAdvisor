import CoreML
import Foundation
import VisitAdvisorKit

/// Developer compute profiles; `.all` is the production candidate.
enum ComputeProfile: String, CaseIterable, Identifiable, Sendable {
    case automatic, cpuOnly, cpuAndGPU, cpuAndNeuralEngine
    var id: String { rawValue }
    var units: MLComputeUnits {
        switch self {
        case .automatic: .all
        case .cpuOnly: .cpuOnly
        case .cpuAndGPU: .cpuAndGPU
        case .cpuAndNeuralEngine: .cpuAndNeuralEngine
        }
    }
}

enum SearchProfile: String, CaseIterable, Identifiable, Sendable {
    case accelerate, metal
    var id: String { rawValue }
}

struct StageTimings: Sendable {
    var preprocessMs = 0.0, proposalMs = 0.0, encoderMs = 0.0, searchMs = 0.0, verificationMs = 0.0, totalMs = 0.0
    var modelCalls = 0
}

struct FrameResult: Sendable {
    let context: RecognitionContext
    let frameID: Int
    let captureTime: Double
    let candidates: [ExhibitCandidate]
    let decision: FrameDecision
    let timings: StageTimings
}

enum EngineError: LocalizedError {
    case modelMissing, invalidModelOutput, verifierUnavailable

    var errorDescription: String? {
        switch self {
        case .modelMissing: "The recognition model is not installed in this build."
        case .invalidModelOutput: "The recognition model produced an invalid result."
        case .verifierUnavailable: "Painting verification is unavailable."
        }
    }
}

/// Owns one validated package, the Core ML model and the search backend for
/// a whole session. A new package means a new engine (new generation), so a
/// prediction never mixes matrices, mappings or models.
actor RecognitionEngine {
    let package: VenuePackage
    let context: RecognitionContext
    private let model: MLModel
    private let search: any VectorSearchBackend
    private var referenceFeatures: [String: [Int: VALocalFeatures]] = [:]

    init(package: VenuePackage, context: RecognitionContext,
         compute: ComputeProfile = .automatic, searchProfile: SearchProfile = .accelerate) throws {
        guard let url = Bundle.main.url(forResource: "DINOv2SmallCLS", withExtension: "mlmodelc") else {
            throw EngineError.modelMissing
        }
        let configuration = MLModelConfiguration()
        configuration.computeUnits = compute.units
        model = try MLModel(contentsOf: url, configuration: configuration)
        self.package = package
        self.context = context
        switch searchProfile {
        case .accelerate: search = AccelerateExactSearch(matrix: package.matrix)
        case .metal: search = (try? MetalExactSearch(matrix: package.matrix)) ?? AccelerateExactSearch(matrix: package.matrix)
        }
        referenceFeatures = try Self.loadReferenceFeatures(package)
    }

    /// Loads all planar reference SIFT features once per package.
    private static func loadReferenceFeatures(_ package: VenuePackage) throws -> [String: [Int: VALocalFeatures]] {
        let local = package.manifest.localFeatures
        let points = try Data(contentsOf: package.directory.appending(path: local.pointsPath))
        let descriptors = try Data(contentsOf: package.directory.appending(path: local.descriptorsPath))
        var result: [String: [Int: VALocalFeatures]] = [:]
        for view in package.index.localViews {
            let p = points.subdata(in: (view.firstPoint * 8)..<((view.firstPoint + view.count) * 8))
            let d = descriptors.subdata(in: (view.firstPoint * 512)..<((view.firstPoint + view.count) * 512))
            result[view.exhibitID, default: [:]][view.viewIndex] = VALocalFeatures(
                points: p, descriptors: d, count: view.count, width: view.width, height: view.height)
        }
        return result
    }

    /// Runs the model once on zeros; scanning stays disabled until this passes.
    func warm() throws {
        _ = try embed([Float](repeating: 0, count: 3 * 224 * 224))
    }

    private func embed(_ tensor: [Float]) throws -> [Float] {
        let input = try MLMultiArray(shape: [1, 3, 224, 224], dataType: .float32)
        tensor.withUnsafeBufferPointer { source in
            let target = input.dataPointer.bindMemory(to: Float.self, capacity: tensor.count)
            target.update(from: source.baseAddress!, count: tensor.count)
        }
        let output = try model.prediction(from: MLDictionaryFeatureProvider(dictionary: ["pixel_values": input]))
        guard let array = output.featureValue(for: "embedding")?.multiArrayValue, array.count == 384 else {
            throw EngineError.invalidModelOutput
        }
        let vector = (0..<array.count).map { Float(truncating: array[$0]) }
        do { return try normalizedQuery(vector, dimension: 384) } catch { throw EngineError.invalidModelOutput }
    }

    /// Port of `pipeline.recognize` for one 512x512 frame.
    func recognize(frame: RGBImage, frameID: Int, captureTime: Double) async throws -> FrameResult {
        let started = Date()
        func elapsed() -> Double { Date().timeIntervalSince(started) * 1000 }
        var timings = StageTimings()
        let policy = package.policy
        if CameraPreprocessing.isUnreadable(frame) {
            timings.totalMs = elapsed()
            return FrameResult(context: context, frameID: frameID, captureTime: captureTime,
                               candidates: [], decision: .unreadable, timings: timings)
        }
        let budget = Double(policy.inferenceBudgetMs)
        var boxes: [NormalizedBox] = [.fullFrame]
        if policy.objectRegions {
            let proposalStart = Date()
            let small = PillowResample.resize(frame, width: 224, height: 224, filter: .bilinear)
            let found = try VAOpenCVBridge.proposals(forRGB224: Data(small.pixels), maximum: 2)
            boxes += found.map {
                NormalizedBox(x0: $0[0].floatValue, y0: $0[1].floatValue, x1: $0[2].floatValue, y1: $0[3].floatValue)
            }
            timings.proposalMs = Date().timeIntervalSince(proposalStart) * 1000
        }
        var perBox: [[ExhibitCandidate]] = []
        var budgetExceeded = false
        for box in boxes {
            if elapsed() >= budget { budgetExceeded = true; break }
            let preprocessStart = Date()
            let region = box == .fullFrame ? frame : CameraPreprocessing.crop(frame, box: box)
            let tensor = CameraPreprocessing.modelInput(region)
            timings.preprocessMs += Date().timeIntervalSince(preprocessStart) * 1000
            let encoderStart = Date()
            let query = try embed(tensor)
            timings.modelCalls += 1
            timings.encoderMs += Date().timeIntervalSince(encoderStart) * 1000
            if elapsed() >= budget { budgetExceeded = true; break }
            let searchStart = Date()
            let rows = try await search.scores(for: query)
            timings.searchMs += Date().timeIntervalSince(searchStart) * 1000

            // Planar exhibits score only through SIFT/homography on this region.
            let verificationStart = Date()
            var evidence: [String: PlanarEvidence] = [:]
            let planar = package.index.exhibits.values.filter { $0.profile == .planar }.map(\.id).sorted()
            if !planar.isEmpty {
                let local = try VAOpenCVBridge.describeRGB(Data(region.pixels), width: region.width, height: region.height)
                for id in planar {
                    if elapsed() >= budget { budgetExceeded = true; break }
                    var best = PlanarEvidence(score: 0, viewIndex: 0)
                    for (viewIndex, reference) in (referenceFeatures[id] ?? [:]).sorted(by: { $0.key < $1.key }) {
                        let result = try VAOpenCVBridge.evidence(forQuery: local, reference: reference)
                        let score = result.score >= 0.4 ? Float(result.score) : 0
                        if score > best.score { best = PlanarEvidence(score: score, viewIndex: viewIndex) }
                    }
                    evidence[id] = best
                }
            }
            timings.verificationMs += Date().timeIntervalSince(verificationStart) * 1000
            perBox.append(ExhibitRanker.candidates(
                rowScores: rows, index: package.index, box: box, planarEvidence: evidence))
            if budgetExceeded { break }
        }
        let candidates = ExhibitRanker.merge(perBox)
        budgetExceeded = budgetExceeded || elapsed() > budget
        timings.totalMs = elapsed()
        return FrameResult(
            context: context, frameID: frameID, captureTime: captureTime, candidates: candidates,
            decision: decide(candidates, policy: policy, budgetExceeded: budgetExceeded), timings: timings)
    }
}
