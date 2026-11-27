import Foundation
import SwiftUI
import VisitAdvisorKit

enum RecognitionState: Equatable {
    case packageUnavailable
    case preparing
    case ready
    case searching
    case tentative(String)
    case confirmed(String)
    case ambiguous
    case interrupted(String)
}

/// Main-actor UI state. Recognition runs in `RecognitionEngine`; results whose
/// context no longer matches the current session are discarded.
@MainActor
final class RecognitionViewModel: ObservableObject {
    @Published private(set) var state: RecognitionState = .packageUnavailable
    @Published private(set) var lastTimings = StageTimings()
    @Published private(set) var topCandidates: [ExhibitCandidate] = []
    @Published var compute: ComputeProfile = .automatic
    @Published var searchProfile: SearchProfile = .accelerate

    let kind: FrameSourceKind
    let camera = CameraFrameSource()
    let glasses: GlassesFrameSource?
    private var source: FrameSource {
        if kind == .glasses, let glasses { return glasses }
        return camera
    }
    private var engine: RecognitionEngine?
    private var package: VenuePackage?
    private var stability: Stability?
    private var generation = 0
    private var loop: Task<Void, Never>?

    init(kind: FrameSourceKind = .phone, link: GlassesLink? = nil) {
        self.kind = link == nil ? .phone : kind
        glasses = self.kind == .glasses ? link.map(GlassesFrameSource.init(link:)) : nil
        glasses?.onInterrupted = { [weak self] message in self?.interrupt(message) }
    }

    var exhibitContent: ExhibitContent? {
        guard let package, let id = currentExhibitID else { return nil }
        return CatalogContent.exhibit(id, in: package)
    }

    var currentExhibitID: String? {
        switch state {
        case .tentative(let id), .confirmed(let id): id
        default: nil
        }
    }

    func start(package: VenuePackage) async {
        stop()
        generation += 1
        state = .preparing
        let context = RecognitionContext(
            generation: generation, venueID: package.manifest.venueID,
            packageVersion: package.manifest.packageVersion,
            pipelineContractID: package.manifest.pipelineContractID, trackID: 0)
        do {
            try await source.prepare()
            let engine = try RecognitionEngine(
                package: package, context: context, compute: compute, searchProfile: searchProfile)
            try await engine.warm()
            guard context.generation == generation else { return }
            self.engine = engine
            self.package = package
            stability = Stability(policy: package.policy)
            state = .ready
            let frames = source.frames()
            source.start()
            loop = Task { [weak self] in
                for await frame in frames {
                    guard !Task.isCancelled else { break }
                    do {
                        let result = try await engine.recognize(
                            frame: frame.image, frameID: frame.id, captureTime: frame.captureTime)
                        await self?.apply(result)
                    } catch {
                        await self?.interrupt(error.localizedDescription)
                    }
                }
            }
        } catch {
            state = .interrupted(error.localizedDescription)
        }
    }

    func stop() {
        loop?.cancel()
        loop = nil
        source.stop()
        generation += 1  // any in-flight result is now stale
        engine = nil
        stability?.reset()
        if case .interrupted = state {} else { state = package == nil ? .packageUnavailable : .ready }
    }

    private func interrupt(_ message: String) {
        state = .interrupted(message)
    }

    private func apply(_ result: FrameResult) {
        // A late prediction must never overwrite a newer session or target.
        guard result.context.generation == generation, var stability else { return }
        lastTimings = result.timings
        topCandidates = result.candidates
        let now = ProcessInfo.processInfo.systemUptime * 1000
        let stable = stability.observe(result.candidates, now: now)
        self.stability = stability
        if let stable {
            // Uncalibrated packages never allow automatic confirmation.
            state = result.decision == .candidate && package?.policy.calibrated == true
                ? .confirmed(stable) : .tentative(stable)
        } else {
            state = result.decision == .ambiguous ? .ambiguous : .searching
        }
    }
}
