import AVFoundation
import MWDATCore
import SwiftUI
import VisitAdvisorKit

@main
struct VisitAdvisorApp: App {
    @StateObject private var library = LibraryModel()
    @StateObject private var glasses: GlassesLink

    init() {
        // Must run before any other Meta Wearables call.
        do { try Wearables.configure() } catch { NSLog("[VisitAdvisor] Wearables.configure failed: \(error)") }
        _glasses = StateObject(wrappedValue: GlassesLink())
    }

    var body: some Scene {
        WindowGroup {
            NavigationStack {
                VenueListView()
            }
            .environmentObject(library)
            .environmentObject(glasses)
            // Meta AI returns here after registration or a camera-permission prompt.
            .onOpenURL { glasses.handle(url: $0) }
            .alert("Glasses", isPresented: Binding(get: { glasses.error != nil }, set: { if !$0 { glasses.error = nil } })) {
                Button("OK") { glasses.error = nil }
            } message: { Text(glasses.error ?? "") }
        }
    }
}

struct VenueListView: View {
    @EnvironmentObject private var library: LibraryModel

    var body: some View {
        List {
            if let error = library.error {
                Text(error).font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(library.venues) { venue in
                NavigationLink(value: venue) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(venue.name).font(.headline)
                        DownloadStatus(state: library.downloads[venue.id] ?? .notInstalled)
                    }
                }
            }
        }
        .navigationTitle("Venues")
        .toolbar {
            NavigationLink { GlassesSettingsView() } label: { GlassesToolbarIcon() }
        }
        .navigationDestination(for: Venue.self) { VenueView(venue: $0) }
        .task { await library.refresh() }
        .refreshable { await library.refresh() }
    }
}

struct DownloadStatus: View {
    let state: LibraryModel.DownloadState

    var body: some View {
        switch state {
        case .notInstalled:
            Label("Not downloaded", systemImage: "icloud.and.arrow.down").font(.caption)
        case .downloading(let fraction):
            ProgressView(value: fraction) { Text("Downloading…").font(.caption) }
        case .installed(_, let bytes):
            Label("Ready offline · \(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file))",
                  systemImage: "checkmark.circle.fill")
                .font(.caption).foregroundStyle(.green)
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
        }
    }
}

struct VenueView: View {
    let venue: Venue
    @EnvironmentObject private var library: LibraryModel
    @EnvironmentObject private var glasses: GlassesLink
    @State private var package: VenuePackage?
    @State private var scanning = false
    /// The camera mode is remembered between launches.
    @AppStorage("cameraMode") private var sourceKind: FrameSourceKind = .phone

    private var state: LibraryModel.DownloadState { library.downloads[venue.id] ?? .notInstalled }

    var body: some View {
        Form {
            Section { DownloadStatus(state: state) }
            Section {
                switch state {
                case .downloading:
                    Button("Cancel download", role: .cancel) { library.cancel(venue) }
                case .installed:
                    Picker("Camera", selection: $sourceKind) {
                        Label("iPhone", systemImage: "iphone").tag(FrameSourceKind.phone)
                        Label("Glasses", systemImage: "eyeglasses").tag(FrameSourceKind.glasses)
                    }
                    .pickerStyle(.segmented)
                    if sourceKind == .glasses {
                        GlassesStatusRow()
                    }
                    if sourceKind == .glasses && !glasses.isRegistered {
                        NavigationLink("Connect Ray-Ban Meta glasses…") { GlassesSettingsView() }
                    }
                    Button("Start scanning") { scan() }
                        .disabled(sourceKind == .glasses && !glasses.isRegistered)
                    Button("Check for update") { library.download(venue) }
                    Button("Delete download", role: .destructive) { Task { await library.delete(venue) } }
                case .failed:
                    Button("Try download again") { library.download(venue) }
                case .notInstalled:
                    Button("Download for offline use") { library.download(venue) }
                }
            } footer: {
                if case .downloading = state {
                    Text("Scanning starts after the download finishes. Recognition then works offline, so download before connecting the glasses (they take over the iPhone's Wi-Fi while streaming).")
                }
            }
        }
        .navigationTitle(venue.name)
        // Selecting a venue starts its download. Only a venue the library has
        // checked and found missing is downloaded; installed ones open as they are.
        .task {
            if library.downloads[venue.id] == .notInstalled { library.download(venue) }
        }
        .fullScreenCover(isPresented: $scanning, onDismiss: {
            if let package { Task { await library.store?.release(package) } }
            package = nil
        }) {
            if let package { CameraScreen(package: package, kind: sourceKind, link: glasses) }
        }
    }

    private func scan() {
        Task {
            package = await library.openPackage(venue)
            scanning = package != nil
        }
    }
}

struct CameraScreen: View {
    let package: VenuePackage
    @StateObject private var model: RecognitionViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showingDetail = false
    @State private var showingDiagnostics = false

    init(package: VenuePackage, kind: FrameSourceKind, link: GlassesLink) {
        self.package = package
        _model = StateObject(wrappedValue: RecognitionViewModel(kind: kind, link: link))
    }

    var body: some View {
        ZStack {
            if let glasses = model.glasses {
                GlassesPreview(source: glasses).ignoresSafeArea()
            } else {
                CameraPreview(session: model.camera.session).ignoresSafeArea()
            }
            // The recognized frame is the centre square of 224/256 of the buffer's
            // short side. Both previews are aspect-fill of a 720x1280 portrait buffer.
            GeometryReader { proxy in
                let scale = max(proxy.size.width / 720, proxy.size.height / 1280)
                let side = 720 * scale * 224 / 256
                RoundedRectangle(cornerRadius: 16)
                    .stroke(.white.opacity(0.8), lineWidth: 2)
                    .frame(width: side, height: side)
                    .position(x: proxy.size.width / 2, y: proxy.size.height / 2)
            }
            VStack {
                HStack {
                    Button { dismiss() } label: { Image(systemName: "xmark.circle.fill").font(.title) }
                    Spacer()
                    Button { showingDiagnostics = true } label: { Image(systemName: "gauge").font(.title2) }
                }
                .foregroundStyle(.white)
                .padding()
                Spacer()
                ResultCard(state: model.state, content: model.exhibitContent) { showingDetail = true }
                    .padding()
            }
        }
        .task { await model.start(package: package) }
        .onDisappear { model.stop() }
        .sheet(isPresented: $showingDetail) {
            if let content = model.exhibitContent { ExhibitDetailView(content: content) }
        }
        .sheet(isPresented: $showingDiagnostics) { DiagnosticsView(model: model, package: package) }
    }
}

struct ResultCard: View {
    let state: RecognitionState
    let content: ExhibitContent?
    let open: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            switch state {
            case .packageUnavailable: Text("Download this venue first.")
            case .preparing: HStack { ProgressView(); Text("Preparing recognition…") }
            case .ready, .searching: Text("Point at an exhibit inside the frame.")
            case .ambiguous: Text("Not sure yet — move closer or centre one object.")
            case .interrupted(let message): Text(message)
            case .tentative, .confirmed:
                if let content {
                    Text(state.isConfirmed ? "Identified" : "Possible match")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(content.name).font(.headline)
                    if let creator = content.creator { Text(creator).font(.subheadline) }
                    Button("Show details", action: open)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
    }
}

extension RecognitionState {
    var isConfirmed: Bool { if case .confirmed = self { true } else { false } }
}

struct ExhibitDetailView: View {
    let content: ExhibitContent

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text(content.name).font(.title2.bold())
                    if let creator = content.creator { Text(creator) }
                    if let year = content.year { Text(year).foregroundStyle(.secondary) }
                }
                if let description = content.description { Section("About") { Text(description) } }
                if !content.sources.isEmpty {
                    Section("Sources") {
                        ForEach(content.sources, id: \.self) { source in
                            if let url = source.url { Link(source.title, destination: url) } else { Text(source.title) }
                        }
                    }
                }
            }
            .navigationTitle(content.type.capitalized)
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

/// Developer-only details; not part of the visitor flow.
struct DiagnosticsView: View {
    @ObservedObject var model: RecognitionViewModel
    let package: VenuePackage

    var body: some View {
        NavigationStack {
            Form {
                Section("Package") {
                    LabeledContent("Version", value: String(package.manifest.packageVersion.prefix(12)))
                    LabeledContent("Pipeline", value: package.manifest.pipelineContractID)
                    LabeledContent("Policy", value: package.policy.calibrated ? "calibrated" : "uncalibrated preview")
                    LabeledContent("References", value: "\(package.matrix.rows) vectors / \(package.manifest.exhibitCount) exhibits")
                }
                Section("Compute (applies on next scan)") {
                    Picker("Core ML", selection: $model.compute) {
                        ForEach(ComputeProfile.allCases) { Text($0.rawValue).tag($0) }
                    }
                    Picker("Vector search", selection: $model.searchProfile) {
                        ForEach(SearchProfile.allCases) { Text($0.rawValue).tag($0) }
                    }
                }
                Section("Last frame (ms)") {
                    let t = model.lastTimings
                    LabeledContent("Proposals", value: t.proposalMs.formatted(.number.precision(.fractionLength(1))))
                    LabeledContent("Preprocess", value: t.preprocessMs.formatted(.number.precision(.fractionLength(1))))
                    LabeledContent("Encoder ×\(t.modelCalls)", value: t.encoderMs.formatted(.number.precision(.fractionLength(1))))
                    LabeledContent("Search", value: t.searchMs.formatted(.number.precision(.fractionLength(2))))
                    LabeledContent("Verification", value: t.verificationMs.formatted(.number.precision(.fractionLength(1))))
                    LabeledContent("Total", value: t.totalMs.formatted(.number.precision(.fractionLength(1))))
                }
                Section("Raw scores (raw cosine / planar geometry)") {
                    ForEach(model.topCandidates, id: \.exhibitID) { candidate in
                        LabeledContent(String(candidate.exhibitID.suffix(8)),
                                       value: candidate.score.formatted(.number.precision(.fractionLength(3))))
                    }
                }
            }
            .navigationTitle("Diagnostics")
        }
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}
}

/// Mirror of what the glasses see. The wearer aims with their head; the square
/// marks the region that is recognized.
struct GlassesPreview: View {
    @ObservedObject var source: GlassesFrameSource

    var body: some View {
        ZStack {
            Color.black
            if let image = source.preview {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                VStack(spacing: 12) {
                    ProgressView().tint(.white)
                    Text(waitingText).foregroundStyle(.white).font(.callout)
                }
            }
            if source.streamState == .paused {
                Text("Paused — tap the glasses to resume")
                    .padding(8).background(.ultraThinMaterial, in: Capsule())
            }
        }
    }

    private var waitingText: String {
        switch source.streamState {
        case .waitingForDevice: "Waiting for glasses…"
        case .starting: "Starting glasses camera…"
        default: "Connecting to glasses…"
        }
    }
}

struct GlassesSettingsView: View {
    @EnvironmentObject private var glasses: GlassesLink

    var body: some View {
        Form {
            Section {
                LabeledContent("Status") {
                    HStack(spacing: 6) {
                        Circle().fill(glasses.readiness.color).frame(width: 10, height: 10)
                        Text(glasses.statusText)
                    }
                }
                LabeledContent("Devices", value: "\(glasses.deviceCount)")
            }
            Section {
                switch glasses.registration {
                case .registered:
                    Button("Disconnect VisitAdvisor from glasses", role: .destructive) { glasses.disconnect() }
                case .registering:
                    HStack { ProgressView(); Text("Finish in the Meta AI app…") }
                default:
                    Button("Connect Ray-Ban Meta glasses") { glasses.connect() }
                }
            } footer: {
                Text("Opens the Meta AI app to approve VisitAdvisor. The glasses need firmware v20 or later, and Developer Mode must be on in Meta AI while VisitAdvisor is in testing. Camera frames are recognized on this iPhone and are not uploaded or stored.")
            }
            #if DEBUG
            Section("Development") {
                if glasses.mockEnabled {
                    Text("Simulated glasses use the iPhone back camera.").font(.footnote)
                    Button("Remove simulated glasses", role: .destructive) { glasses.disableMockGlasses() }
                } else {
                    Button("Use simulated glasses (Mock Device Kit)") { glasses.enableMockGlasses() }
                }
            }
            #endif
        }
        .navigationTitle("Glasses")
    }
}

/// Glasses icon with a status dot: green ready, orange wear/connecting,
/// grey not connected, red Meta AI unavailable.
struct GlassesToolbarIcon: View {
    @EnvironmentObject private var glasses: GlassesLink

    var body: some View {
        Image(systemName: "eyeglasses")
            .overlay(alignment: .topTrailing) {
                Circle()
                    .fill(glasses.readiness.color)
                    .frame(width: 9, height: 9)
                    .overlay(Circle().stroke(Color(.systemBackground), lineWidth: 1.5))
                    .offset(x: 4, y: -3)
            }
            .accessibilityLabel(glasses.readiness.title)
    }
}

struct GlassesStatusRow: View {
    @EnvironmentObject private var glasses: GlassesLink

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(glasses.readiness.color).frame(width: 10, height: 10)
            Text(glasses.readiness.title).font(.subheadline)
        }
        .accessibilityElement(children: .combine)
    }
}
