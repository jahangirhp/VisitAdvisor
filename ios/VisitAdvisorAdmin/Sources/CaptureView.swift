import SwiftUI

/// Guided reference capture: one photo per requested angle, reviewed on the
/// phone (original vs. background removed), then uploaded and prepared.
struct CaptureView: View {
    let api: AdminAPI
    let venue: AdminVenue
    let objectID: String
    let objectType: String
    let sourceID: String?
    let onFinish: () -> Void

    @State private var camera = AdminCamera()
    @State private var index = 0
    @State private var photos: [CapturedPhoto] = []
    @State private var reviewing: CapturedPhoto?
    @State private var busy: String?
    @State private var error: String?
    @State private var uploaded = false
    private let sessionID = "ios-" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    private let captureGroup = "admin-ios-\(UUID().uuidString)"

    private var profile: String { isPlanar(objectType) ? "planar" : "multiview" }
    private var views: [CaptureAngle] { CaptureAngle.plan(planar: isPlanar(objectType)) }
    private var current: CaptureAngle? { index < views.count ? views[index] : nil }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            AdminCameraPreview(session: camera.session).ignoresSafeArea()
            VStack(spacing: 12) {
                instruction
                Spacer()
                thumbnails
                controls
            }
            .padding()
            if let busy {
                ProgressView(busy).tint(.white).foregroundStyle(.white)
                    .padding().background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 12))
            }
        }
        .navigationBarBackButtonHidden(busy != nil)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Close") { camera.stop(); onFinish() }.disabled(busy != nil) }
        }
        .sheet(item: $reviewing) { photo in
            ReviewSheet(photo: photo) { accepted in
                if let accepted {
                    photos.append(accepted)
                    index += 1
                }
                reviewing = nil
            }
        }
        .alert("Problem", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("OK") { error = nil }
        } message: { Text(error ?? "") }
        .task {
            do { try await camera.start(withVideoFrames: false) } catch { self.error = error.localizedDescription }
        }
        .onDisappear { camera.stop() }
    }

    private var instruction: some View {
        VStack(spacing: 4) {
            if uploaded {
                Text("Saved and prepared").font(.headline)
                Text("Use “Test recognition score” to check how well it is recognised.").font(.caption)
            } else if let current {
                Text("Photo \(index + 1) of \(views.count): \(current.label)").font(.headline)
                Text("Stand where visitors stand. Object fills about half to two-thirds of the screen, in its usual light.")
                    .font(.caption).multilineTextAlignment(.center)
            } else {
                Text("All angles taken").font(.headline)
                Text("Upload them, or add more angles later.").font(.caption)
            }
        }
        .foregroundStyle(.white)
        .padding(10)
        .frame(maxWidth: .infinity)
        .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 12))
    }

    private var thumbnails: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack {
                ForEach(photos) { photo in
                    Image(uiImage: (photo.useMask ? photo.cutout : nil) ?? photo.preview)
                        .resizable().scaledToFill().frame(width: 56, height: 56)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                        .overlay(alignment: .bottomTrailing) {
                            if photo.useMask { Image(systemName: "person.crop.square.filled.and.at.rectangle").font(.caption2).padding(2) }
                        }
                }
            }
        }
        .frame(height: photos.isEmpty ? 0 : 60)
    }

    private var controls: some View {
        HStack {
            Button("Skip angle") { index += 1 }
                .disabled(current == nil || busy != nil || uploaded)
            Spacer()
            Button { Task { await shoot() } } label: {
                Circle().fill(.white).frame(width: 72, height: 72)
                    .overlay(Circle().stroke(.black.opacity(0.3), lineWidth: 2).padding(4))
            }
            .disabled(busy != nil || uploaded)
            .accessibilityLabel("Take photo")
            Spacer()
            Button(uploaded ? "Done" : "Upload \(photos.count)") {
                if uploaded { camera.stop(); onFinish() } else { Task { await upload() } }
            }
            .disabled((!uploaded && photos.isEmpty) || busy != nil)
            .fontWeight(.semibold)
        }
        .foregroundStyle(.white)
        .padding(.horizontal)
    }

    private func shoot() async {
        busy = "Processing photo…"
        defer { busy = nil }
        do {
            let data = try await camera.capturePhoto()
            let view = current ?? .front
            let profile = profile, sessionID = sessionID
            reviewing = try await Task.detached(priority: .userInitiated) {
                try PhotoProcessor.process(photoData: data, view: view, profile: profile, sessionID: sessionID)
            }.value
        } catch { self.error = error.localizedDescription }
    }

    private func upload() async {
        for (n, photo) in photos.enumerated() {
            busy = "Uploading \(n + 1) of \(photos.count)…"
            do {
                try await api.upload(venue: venue.id, object: objectID, photo: photo, captureGroup: captureGroup, sourceID: sourceID)
            } catch let failure as AdminError where failure.code == "DUPLICATE_PHOTO" {
                continue
            } catch {
                busy = nil
                self.error = "Photo \(n + 1): \(error.localizedDescription)"
                photos.removeFirst(n)  // keep only the ones not yet uploaded for a retry
                return
            }
        }
        busy = "Preparing for recognition…"
        do {
            try await api.save(venue: venue.id, object: objectID)
            uploaded = true
            photos = []
        } catch {
            self.error = "Photos uploaded, but preparation failed: \(error.localizedDescription). Use “Prepare waiting photos” on the object page."
            photos = []
        }
        busy = nil
    }
}

struct ReviewSheet: View {
    @State var photo: CapturedPhoto
    let done: (CapturedPhoto?) -> Void

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    HStack(spacing: 8) {
                        labelled("Original (always kept)", photo.preview)
                        if let cutout = photo.cutout { labelled("Background removed", cutout) }
                    }
                    if photo.hasMask {
                        Toggle("Use background-removed version for recognition", isOn: $photo.useMask)
                        Text(photo.useMask
                             ? "The original and its mask are both saved. Recognition currently uses the cut-out on white."
                             : "Only the full photo is used, background included.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text("No separate object found, so the full photo is used.").font(.caption).foregroundStyle(.secondary)
                    }
                    qualityNote
                }
                .padding()
            }
            .navigationTitle(photo.view.label)
            .navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 12) {
                    Button { done(nil) } label: {
                        Label("Retake", systemImage: "arrow.counterclockwise").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    Button { done(photo) } label: {
                        Label("Use photo", systemImage: "checkmark").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                }
                .controlSize(.large)
                .padding()
                .background(.bar)
            }
            .interactiveDismissDisabled()
        }
    }

    private func labelled(_ title: String, _ image: UIImage) -> some View {
        VStack {
            Image(uiImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 8))
            Text(title).font(.caption)
        }
    }

    @ViewBuilder private var qualityNote: some View {
        if photo.quality.sharpness < 20 {
            Label("Looks blurry. Hold still and retake.", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
        }
        if photo.quality.clipped > 0.25 {
            Label("Large areas are too bright or too dark.", systemImage: "sun.max").foregroundStyle(.orange)
        }
    }
}
