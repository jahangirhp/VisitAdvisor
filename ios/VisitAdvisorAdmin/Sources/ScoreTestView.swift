import SwiftUI

/// Live recognition check against the server's current references: the same
/// `/live/recognize` request and frame crop the visitor website uses, so a
/// score here is what the website would compute right now.
@MainActor
final class ScoreTestModel: ObservableObject {
    @Published private(set) var catalog: LiveCatalog?
    @Published private(set) var candidates: [LiveResult.Candidate] = []
    @Published private(set) var inferenceMs: Double?
    @Published var error: String?

    let api: AdminAPI
    let venueID: String
    let camera = AdminCamera()
    private let gate = SubmitGate()

    init(api: AdminAPI, venueID: String) {
        self.api = api
        self.venueID = venueID
    }

    func start() async {
        do {
            catalog = try await api.liveCatalog(venue: venueID)
            try await camera.start(withVideoFrames: true)
        } catch {
            self.error = error.localizedDescription
            return
        }
        let size = catalog?.policy.frameSize ?? 224
        let gate = gate
        camera.setFrameHandler { [weak self] buffer in
            // Camera queue: at most one request in flight, about two per second.
            guard gate.tryBegin(), let jpeg = LiveFrame.jpeg(from: buffer, size: size) else { return }
            Task { @MainActor in
                await self?.send(jpeg)
                gate.end()
            }
        }
    }

    func stop() { camera.stop() }

    private func send(_ jpeg: Data) async {
        guard let catalog else { return }
        do {
            let result = try await api.recognize(venue: venueID, referenceVersion: catalog.referenceVersion, jpeg: jpeg)
            candidates = result.candidates
            inferenceMs = result.inferenceMs
            error = nil
        } catch let failure as AdminError where failure.status == 429 {
            return  // server busy; next frame
        } catch let failure as AdminError where failure.code == "REFERENCES_CHANGED" {
            self.catalog = try? await api.liveCatalog(venue: venueID)  // photos were just prepared
        } catch {
            self.error = error.localizedDescription
        }
    }

    func name(_ id: String) -> String { catalog?.objects.first { $0.id == id }?.name ?? String(id.suffix(8)) }
    var floor: Double { catalog?.policy.candidateFloor ?? 0.55 }
    var margin: Double { catalog?.policy.candidateMargin ?? 0.04 }

    /// Same acceptance test as the visitor camera: top score above the floor
    /// and ahead of the runner-up by the margin.
    var verdict: (text: String, good: Bool)? {
        guard let first = candidates.first else { return nil }
        let second = candidates.dropFirst().first?.score ?? 0
        if first.score < floor { return ("Below the \(String(format: "%.2f", floor)) bar", false) }
        if first.score - second < margin { return ("Too close to \(name(candidates[1].id))", false) }
        return ("Would be suggested: \(name(first.id))", true)
    }
}

final class SubmitGate: @unchecked Sendable {
    private let lock = NSLock()
    private var busy = false
    private var last = 0.0
    func tryBegin() -> Bool {
        let now = ProcessInfo.processInfo.systemUptime
        return lock.withLock {
            guard !busy, now - last >= 0.45 else { return false }
            busy = true
            last = now
            return true
        }
    }
    func end() { lock.withLock { busy = false } }
}

struct ScoreTestView: View {
    let api: AdminAPI
    let venue: AdminVenue
    let focusObjectID: String?
    @StateObject private var model: ScoreTestModel

    init(api: AdminAPI, venue: AdminVenue, focusObjectID: String?) {
        self.api = api
        self.venue = venue
        self.focusObjectID = focusObjectID
        _model = StateObject(wrappedValue: ScoreTestModel(api: api, venueID: venue.id))
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            AdminCameraPreview(session: model.camera.session, fill: false).ignoresSafeArea()
            GeometryReader { proxy in
                // The whole 3:4 camera frame is shown (aspect fit); the square is the
                // centre 224/256 of its short side, exactly what is sent.
                let side = min(proxy.size.width / 3, proxy.size.height / 4) * 3 * 224 / 256
                RoundedRectangle(cornerRadius: 12).stroke(.white.opacity(0.8), lineWidth: 2)
                    .frame(width: side, height: side)
                    .position(x: proxy.size.width / 2, y: proxy.size.height / 2)
            }
            .ignoresSafeArea()
            VStack {
                Spacer()
                results.padding()
            }
        }
        .navigationTitle("Recognition test")
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.start() }
        .onDisappear { model.stop() }
    }

    private var results: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let error = model.error {
                Text(error).foregroundStyle(.red)
            } else if model.candidates.isEmpty {
                Text("Point at an object inside the square.")
            }
            ForEach(model.candidates, id: \.id) { candidate in
                ScoreBar(name: model.name(candidate.id), score: candidate.score, floor: model.floor,
                         highlighted: candidate.id == focusObjectID)
            }
            if let verdict = model.verdict {
                Label(verdict.text, systemImage: verdict.good ? "checkmark.circle.fill" : "xmark.circle")
                    .foregroundStyle(verdict.good ? .green : .orange).font(.subheadline.bold())
            }
            if let ms = model.inferenceMs {
                Text("Server pipeline, \(Int(ms)) ms").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
    }
}

struct ScoreBar: View {
    let name: String
    let score: Double
    let floor: Double
    let highlighted: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(name).fontWeight(highlighted ? .bold : .regular)
                Spacer()
                Text(String(format: "%.3f", score)).monospacedDigit()
            }
            .font(.subheadline)
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(.gray.opacity(0.25))
                    Capsule().fill(score >= floor ? Color.green : Color.orange)
                        .frame(width: proxy.size.width * max(0, min(1, score)))
                    Rectangle().fill(.primary).frame(width: 2)
                        .offset(x: proxy.size.width * floor - 1)
                }
            }
            .frame(height: 8)
        }
    }
}
