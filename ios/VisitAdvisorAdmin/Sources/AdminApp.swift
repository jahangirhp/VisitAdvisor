import SwiftUI

@main
struct VisitAdvisorAdminApp: App {
    @StateObject private var session = AdminSession()

    var body: some Scene {
        WindowGroup {
            Group {
                if let api = session.api {
                    NavigationStack { VenuesView(api: api) }
                } else {
                    UnlockView()
                }
            }
            .environmentObject(session)
        }
    }
}

/// Holds the unlocked API client. The token is checked against the server
/// before it is kept.
@MainActor
final class AdminSession: ObservableObject {
    @Published private(set) var api: AdminAPI?
    @Published var error: String?
    @Published var checking = false

    init() {
        if let token = TokenStore.load() { api = AdminAPI(baseURL: AdminAPI.configuredBaseURL, token: token) }
    }

    func unlock(_ token: String) async {
        let token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { return }
        checking = true
        defer { checking = false }
        let candidate = AdminAPI(baseURL: AdminAPI.configuredBaseURL, token: token)
        do {
            _ = try await candidate.venues()
            TokenStore.save(token)
            error = nil
            api = candidate
        } catch {
            self.error = error.localizedDescription
        }
    }

    func lock() {
        TokenStore.clear()
        api = nil
    }
}

struct UnlockView: View {
    @EnvironmentObject private var session: AdminSession
    @State private var token = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("Admin token", text: $token)
                        .textContentType(.password)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    Button {
                        Task { await session.unlock(token) }
                    } label: {
                        HStack { Text("Unlock library"); if session.checking { Spacer(); ProgressView() } }
                    }
                    .disabled(token.isEmpty || session.checking)
                } footer: {
                    Text("Stored only in this iPhone's Keychain. Server: \(AdminAPI.configuredBaseURL.host() ?? "")")
                }
                if let error = session.error {
                    Section { Text(error).foregroundStyle(.red) }
                }
            }
            .navigationTitle("VisitAdvisor Admin")
        }
    }
}

// MARK: - Venues and objects

struct VenuesView: View {
    let api: AdminAPI
    @EnvironmentObject private var session: AdminSession
    @State private var venues: [AdminVenue] = []
    @State private var error: String?

    var body: some View {
        List {
            if let error { Text(error).foregroundStyle(.red) }
            ForEach(venues) { venue in
                NavigationLink(value: venue) {
                    VStack(alignment: .leading) {
                        Text(venue.name).font(.headline)
                        if let city = venue.city, !city.isEmpty { Text(city).font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
        }
        .navigationTitle("Venues")
        .navigationDestination(for: AdminVenue.self) { ObjectsView(api: api, venue: $0) }
        .toolbar { Button("Lock") { session.lock() } }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do { venues = try await api.venues(); error = nil } catch { self.error = error.localizedDescription }
    }
}

struct ObjectsView: View {
    let api: AdminAPI
    let venue: AdminVenue
    @State private var objects: [AdminObjectRow] = []
    @State private var error: String?
    @State private var creating = false
    @State private var showGaps = false

    private var shown: [AdminObjectRow] { showGaps ? objects.filter(\.needsPhotos) : objects }

    var body: some View {
        List {
            if let error { Text(error).foregroundStyle(.red) }
            Toggle("Only objects needing photos", isOn: $showGaps)
            ForEach(shown) { object in
                NavigationLink(value: object) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(object.name).font(.headline)
                            Text(object.type.capitalized).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        PhotoProgress(row: object)
                    }
                }
            }
        }
        .navigationTitle(venue.name)
        .navigationDestination(for: AdminObjectRow.self) { ObjectDetailView(api: api, venue: venue, objectID: $0.id) }
        .toolbar {
            Button { creating = true } label: { Image(systemName: "plus") }
            NavigationLink { ScoreTestView(api: api, venue: venue, focusObjectID: nil) } label: { Image(systemName: "scope") }
        }
        .sheet(isPresented: $creating, onDismiss: { Task { await load() } }) {
            NewObjectView(api: api, venue: venue)
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do { objects = try await api.objects(venue: venue.id); error = nil } catch { self.error = error.localizedDescription }
    }
}

struct PhotoProgress: View {
    let row: AdminObjectRow
    var body: some View {
        let have = row.prepared + row.pending
        Text("\(have)/\(row.target)")
            .font(.caption.monospacedDigit())
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(row.needsPhotos ? Color.orange.opacity(0.2) : Color.green.opacity(0.2), in: Capsule())
            .accessibilityLabel("\(have) of \(row.target) photos")
    }
}

struct NewObjectView: View {
    let api: AdminAPI
    let venue: AdminVenue
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var kind: ObjectKind = .object3D
    @State private var type = "sculpture"
    @State private var year = ""
    @State private var description = ""
    @State private var saving = false
    @State private var error: String?
    @State private var created: CreatedObject?

    var body: some View {
        NavigationStack {
            Form {
                TextField("Name", text: $name)
                Section {
                    Picker("Kind", selection: $kind) {
                        ForEach(ObjectKind.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: kind) { _, new in type = new.types[0].value }
                    Picker("Shown to visitors as", selection: $type) {
                        ForEach(kind.types, id: \.value) { Text($0.label).tag($0.value) }
                    }
                } footer: {
                    Text(kind.help)
                }
                Section("Optional") {
                    TextField("Year or period", text: $year)
                    TextField("Description", text: $description, axis: .vertical).lineLimit(3...6)
                }
                if let error { Text(error).foregroundStyle(.red) }
            }
            .navigationTitle("New object")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create & photograph") { Task { await create() } }
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || saving)
                }
            }
            .navigationDestination(item: $created) { object in
                CaptureView(api: api, venue: venue, objectID: object.id, objectType: object.type,
                            sourceID: object.sourceIDs.last) { dismiss() }
            }
        }
    }

    private func create() async {
        saving = true
        defer { saving = false }
        do {
            created = try await api.createObject(venue: venue.id, name: name.trimmingCharacters(in: .whitespaces), type: type,
                                                 year: year.trimmingCharacters(in: .whitespaces),
                                                 description: description.trimmingCharacters(in: .whitespaces))
        } catch { self.error = error.localizedDescription }
    }
}

extension CreatedObject: Hashable, Identifiable {
    static func == (a: CreatedObject, b: CreatedObject) -> Bool { a.id == b.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

// MARK: - Object detail

struct ObjectDetailView: View {
    let api: AdminAPI
    let venue: AdminVenue
    let objectID: String
    @Environment(\.dismiss) private var dismiss
    @State private var detail: AdminObjectDetail?
    @State private var error: String?
    @State private var capturing = false
    @State private var confirmDelete = false
    @State private var working = false

    var body: some View {
        List {
            if let error { Text(error).foregroundStyle(.red) }
            if let detail {
                Section {
                    LabeledContent("Type", value: detail.type.capitalized)
                    LabeledContent("Photos", value: "\(detail.prepared) ready, \(detail.pending) waiting · target \(detail.target)")
                    if detail.pending > 0 {
                        Button("Prepare waiting photos for recognition") { Task { await prepare() } }.disabled(working)
                    }
                    if detail.stale { Text("Details changed since the last preparation.").font(.caption).foregroundStyle(.orange) }
                }
                Section("Saved photos") {
                    if detail.photos.isEmpty { Text("No photos yet.").foregroundStyle(.secondary) }
                    ForEach(detail.photos) { photo in
                        SavedPhotoRow(api: api, venueID: venue.id, objectID: objectID, photo: photo)
                            .swipeActions {
                                Button("Delete", role: .destructive) { Task { await deletePhoto(photo) } }
                            }
                    }
                }
                Section {
                    Button { capturing = true } label: { Label("Add photos", systemImage: "camera") }
                    NavigationLink {
                        ScoreTestView(api: api, venue: venue, focusObjectID: objectID)
                    } label: { Label("Test recognition score", systemImage: "scope") }
                }
                Section {
                    Button("Delete object", role: .destructive) { confirmDelete = true }
                }
            } else if error == nil {
                ProgressView()
            }
        }
        .navigationTitle(detail?.name ?? "Object")
        .overlay { if working { ProgressView("Working…").padding().background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12)) } }
        .fullScreenCover(isPresented: $capturing, onDismiss: { Task { await load() } }) {
            if let detail {
                NavigationStack {
                    CaptureView(api: api, venue: venue, objectID: objectID, objectType: detail.type,
                                sourceID: detail.sources.last?.id,
                                existing: detail.prepared + detail.pending) { capturing = false }
                }
            }
        }
        .confirmationDialog("Delete \(detail?.name ?? "this object") and all its photos? This cannot be undone.",
                            isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete permanently", role: .destructive) { Task { await deleteObject() } }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        do { detail = try await api.detail(venue: venue.id, object: objectID); error = nil } catch { self.error = error.localizedDescription }
    }

    private func prepare() async {
        working = true
        defer { working = false }
        do { try await api.save(venue: venue.id, object: objectID); await load() } catch { self.error = error.localizedDescription }
    }

    private func deletePhoto(_ photo: AdminPhoto) async {
        do { try await api.deletePhoto(venue: venue.id, object: objectID, photo: photo.id); await load() }
        catch { self.error = error.localizedDescription }
    }

    private func deleteObject() async {
        working = true
        defer { working = false }
        do { try await api.deleteObject(venue: venue.id, object: objectID); dismiss() } catch { self.error = error.localizedDescription }
    }
}

struct SavedPhotoRow: View {
    let api: AdminAPI
    let venueID: String
    let objectID: String
    let photo: AdminPhoto
    @State private var image: UIImage?
    @State private var mask: UIImage?
    @State private var showMask = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                Color(.secondarySystemBackground)
                if let shown = showMask ? mask : image { Image(uiImage: shown).resizable().scaledToFit() }
            }
            .frame(width: 96, height: 96)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 4) {
                Text(photo.viewpoint ?? "Unlabelled").font(.subheadline)
                Text(photo.preparationLabel).font(.caption)
                    .foregroundStyle(photo.hasMask ? .blue : .secondary)
                if photo.hasMask {
                    Button(showMask ? "Show photo" : "Show mask") {
                        showMask.toggle()
                        if showMask && mask == nil { Task { await loadMask() } }
                    }
                    .font(.caption).buttonStyle(.borderless)
                }
            }
        }
        .task {
            if let data = try? await api.photoData(venue: venueID, object: objectID, photo: photo.id) { image = UIImage(data: data) }
        }
    }

    private func loadMask() async {
        if let data = try? await api.photoData(venue: venueID, object: objectID, photo: photo.id, mask: true) { mask = UIImage(data: data) }
    }
}

/// The three ways recognition treats an object (server: planar, multiview,
/// scene); the exact type is still stored and shown to visitors.
enum ObjectKind: String, CaseIterable, Identifiable {
    case flat, object3D, scene
    var id: String { rawValue }

    var title: String {
        switch self {
        case .flat: "2D artwork"
        case .object3D: "3D object"
        case .scene: "Room / building"
        }
    }

    var types: [(value: String, label: String)] {
        switch self {
        case .flat: [("painting", "Painting"), ("drawing", "Drawing"), ("print", "Print"), ("photograph", "Photograph")]
        case .object3D: [("sculpture", "Sculpture / statue"), ("artifact", "Artifact / exhibit"), ("other", "Other object")]
        case .scene: [("room", "Room / interior"), ("building", "Building / architecture")]
        }
    }

    var help: String {
        switch self {
        case .flat: "Matched by its details. 3 photos: straight on and at 45° each side."
        case .object3D: "Matched from several angles. 4 photos: front, 45° each side and back."
        case .scene: "Matched as a whole view; the full photo is always used."
        }
    }
}
