import AppKit
import Darwin
import SwiftUI

// MARK: - Engine state (written by scripts/ai-harness/harness.py)

struct Attempt: Decodable, Equatable {
    let provider: String
    let status: String
    let reason: String?
}

struct TaskState: Decodable, Identifiable, Equatable {
    let name: String
    let kind: String
    let round: Int
    let role: String
    let provider: String?
    let state: String
    let started: Double?
    let ended: Double?
    let changedFiles: Int?
    let log: String?
    let result: String?
    let patch: String?
    let prompt: String?
    let error: String?
    let ownedPaths: [String]?
    let attempts: [Attempt]
    var id: String { name }
}

struct VerifyResult: Decodable, Equatable {
    let command: String
    let round: Int
    let passed: Bool
    let exit: Int
}

struct EventItem: Decodable, Equatable {
    let t: Double
    let msg: String
}

struct RunState: Decodable, Identifiable, Equatable {
    let id: String
    let command: String
    let scope: [String]?
    let status: String
    let round: Int
    let maxRounds: Int
    let pid: Int32?
    let started: Double
    let ended: Double?
    let error: String?
    let summary: String?
    let report: String?
    let verify: VerifyResult?
    let completed: Bool?
    let feedback: String?
    let finalFiles: [String]?
    let tasks: [TaskState]
    let events: [EventItem]

    static let activeStatuses: Set<String> = ["queued", "snapshot", "planning", "working", "security-review", "reviewing", "integrating", "verifying", "summarizing"]

    var engineAlive: Bool {
        guard let pid else { return false }
        return kill(pid, 0) == 0 || errno == EPERM
    }
    var isActive: Bool { Self.activeStatuses.contains(status) && engineAlive }
    var isFinished: Bool { ["ready", "no-changes", "applied"].contains(status) }
    /// Review said done and verification (if any) passed.
    var allDone: Bool { isFinished && completed == true && verify?.passed != false }
    /// Failed, stopped, or the engine died mid-run.
    var canRetry: Bool {
        status == "failed" || status == "stopped" || (Self.activeStatuses.contains(status) && !engineAlive)
    }
    var displayStatus: String {
        Self.activeStatuses.contains(status) && !engineAlive ? "\(status) (engine gone)" : status
    }
}

struct ProviderInfo: Identifiable, Equatable {
    let id: String
    let label: String
    let model: String
    let enabled: Bool
    let fallback: [String]
}

func statusColor(_ status: String) -> Color {
    switch status {
    case "ready", "done", "integrated", "passed": .green
    case "applied": .blue
    case "no-changes", "pending", "rejected": .secondary
    case "failed", "stopped", "conflict", "out-of-scope": .red
    default: status.contains("engine gone") ? .red : .orange
    }
}

func statusSymbol(_ status: String) -> String {
    switch status {
    case "ready", "done", "integrated", "passed": "checkmark.circle.fill"
    case "applied": "arrow.down.doc.fill"
    case "no-changes", "pending": "circle.dashed"
    case "rejected": "minus.circle"
    case "failed", "stopped", "conflict", "out-of-scope": "xmark.octagon.fill"
    default: status.contains("engine gone") ? "exclamationmark.triangle.fill" : "circle.dotted.circle"
    }
}

func formatDuration(_ start: Double?, _ end: Double?) -> String {
    guard let start else { return "–" }
    let s = Int((end ?? Date().timeIntervalSince1970) - start)
    if s >= 3600 { return String(format: "%dh%02dm", s / 3600, s % 3600 / 60) }
    if s >= 60 { return String(format: "%dm%02ds", s / 60, s % 60) }
    return "\(s)s"
}

enum Files {
    static let ansi = try! NSRegularExpression(pattern: "\u{1B}\\[[0-9;?]*[A-Za-z]")

    static func tail(_ url: URL, maxBytes: Int = 200_000) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        try? handle.seek(toOffset: size > UInt64(maxBytes) ? size - UInt64(maxBytes) : 0)
        let text = String(decoding: (try? handle.readToEnd()) ?? Data(), as: UTF8.self)
        return ansi.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: "")
    }
}

// MARK: - Engine bridge

enum Engine {
    static let home = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".ai-harness")
    static let script = home.appendingPathComponent("bin/harness.py")
    static let config = home.appendingPathComponent("config.json")

    /// Runs harness.py through a login shell so the user's PATH (codex, opencode, claude, agy) is available.
    static func run(_ args: [String], completion: @escaping @MainActor (Int32, String) -> Void) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-lc", "exec \"$(command -v python3 || echo /usr/bin/python3)\" \"$0\" \"$@\"", script.path] + args
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        process.terminationHandler = { proc in
            let output = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            Task { @MainActor in completion(proc.terminationStatus, output.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        do {
            try process.run()
        } catch {
            Task { @MainActor in completion(-1, error.localizedDescription) }
        }
    }

    /// Installs the engine bundled in the app (Resources/harness.py) when missing or outdated.
    static func bootstrap(completion: @escaping @MainActor () -> Void) {
        guard let bundled = Bundle.main.url(forResource: "harness", withExtension: "py"),
              let data = try? Data(contentsOf: bundled) else {
            Task { @MainActor in completion() }
            return
        }
        let installed = try? Data(contentsOf: script)
        let needsInit = !FileManager.default.fileExists(atPath: config.path)
        if installed != data {
            try? FileManager.default.createDirectory(at: script.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? data.write(to: script, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)
        }
        if installed != data || needsInit {
            run(["init"]) { _, _ in completion() }
        } else {
            Task { @MainActor in completion() }
        }
    }

    static func readConfig() -> [String: Any] {
        guard let data = try? Data(contentsOf: config),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return json
    }

    static func providers() -> [ProviderInfo] {
        let json = readConfig()
        let providers = json["providers"] as? [String: [String: Any]] ?? [:]
        let fallback = json["fallback"] as? [String: [String]] ?? [:]
        return ["sol", "luna", "kimi", "claude", "antigravity"].compactMap { id in
            guard let p = providers[id] else { return nil }
            return ProviderInfo(
                id: id,
                label: p["label"] as? String ?? id,
                model: p["model"] as? String ?? "",
                enabled: p["enabled"] as? Bool ?? true,
                fallback: fallback[id] ?? []
            )
        }
    }

    static func setEnabled(_ id: String, _ enabled: Bool) {
        var json = readConfig()
        var providers = json["providers"] as? [String: [String: Any]] ?? [:]
        providers[id, default: [:]]["enabled"] = enabled
        json["providers"] = providers
        guard let data = try? JSONSerialization.data(withJSONObject: json, options: [.prettyPrinted, .sortedKeys]) else { return }
        try? data.write(to: config, options: .atomic)
    }
}

// MARK: - Store

@MainActor
final class HarnessStore: ObservableObject {
    @Published var repoPath: String {
        didSet {
            UserDefaults.standard.set(repoPath, forKey: "repoPath")
            var recent = recentRepos.filter { $0 != repoPath }
            recent.insert(repoPath, at: 0)
            recentRepos = Array(recent.prefix(8))
            UserDefaults.standard.set(recentRepos, forKey: "recentRepos")
            selectedRun = nil
            runs = []
            refresh()
        }
    }
    @Published var recentRepos: [String]
    @Published var runs: [RunState] = []
    @Published var selectedRun: String?
    @Published var providers: [ProviderInfo] = []
    @Published var message: String?
    @Published var engineInstalled = true

    private var timer: Timer?
    private var refreshing = false

    var runsDir: URL { URL(fileURLWithPath: repoPath).appendingPathComponent(".ai-harness/runs") }
    var current: RunState? { runs.first { $0.id == selectedRun } }
    var activeTaskCount: Int { runs.filter(\.isActive).flatMap(\.tasks).filter { $0.state == "running" }.count }

    init() {
        let path = UserDefaults.standard.string(forKey: "repoPath") ?? NSHomeDirectory()
        repoPath = path
        recentRepos = UserDefaults.standard.stringArray(forKey: "recentRepos") ?? [path]
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        Engine.bootstrap { [weak self] in self?.refresh() }
        refresh()
    }

    func refresh() {
        guard !refreshing else { return }
        refreshing = true
        let dir = runsDir
        Task.detached(priority: .utility) {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let ids = ((try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []).sorted(by: >)
            let runs: [RunState] = ids.compactMap { id in
                guard let data = try? Data(contentsOf: dir.appendingPathComponent("\(id)/state.json")) else { return nil }
                return try? decoder.decode(RunState.self, from: data)
            }
            let providers = Engine.providers()
            let installed = FileManager.default.fileExists(atPath: Engine.script.path)
            await MainActor.run {
                self.refreshing = false
                if self.runsDir != dir { return }
                if self.runs != runs { self.runs = runs }
                if self.providers != providers { self.providers = providers }
                self.engineInstalled = installed
                if self.selectedRun == nil || !runs.contains(where: { $0.id == self.selectedRun }) {
                    self.selectedRun = runs.first?.id
                }
            }
        }
    }

    func start(command: String, rounds: Int, paths: [String]) {
        message = "Starting…"
        let pathArgs = paths.flatMap { ["--path", $0] }
        Engine.run(["start", "--repo", repoPath, "--rounds", String(rounds)] + pathArgs + ["--", command]) { code, output in
            if code == 0 {
                self.message = nil
                self.selectedRun = output.split(separator: "\n").last.map(String.init)
                self.refresh()
            } else {
                self.message = "Start failed: \(output)"
            }
        }
    }

    func engineAction(_ action: String, run: String) {
        Engine.run([action, "--repo", repoPath, run]) { code, output in
            self.message = code == 0 ? output : "\(action) failed: \(output)"
            self.refresh()
        }
    }

    func toggle(_ provider: ProviderInfo) {
        Engine.setEnabled(provider.id, !provider.enabled)
        refresh()
    }

    func chooseRepo() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.directoryURL = URL(fileURLWithPath: repoPath)
        panel.prompt = "Use Project"
        if panel.runModal() == .OK, let url = panel.url { repoPath = url.path }
    }

    /// Lets the user pick files/folders inside the project; returns project-relative paths.
    func pickPaths() -> [String] {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.directoryURL = URL(fileURLWithPath: repoPath)
        panel.prompt = "Add"
        guard panel.runModal() == .OK else { return [] }
        let root = URL(fileURLWithPath: repoPath).standardizedFileURL.path + "/"
        return panel.urls.compactMap { url in
            let path = url.standardizedFileURL.path
            guard path.hasPrefix(root) else {
                message = "\(url.lastPathComponent) is outside the project"
                return nil
            }
            return String(path.dropFirst(root.count))
        }
    }

    func reveal(_ url: URL) {
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }
}

// MARK: - Views

struct StatusBadge: View {
    let status: String
    var body: some View {
        Label(status, systemImage: statusSymbol(status))
            .font(.caption.weight(.semibold))
            .foregroundStyle(statusColor(status))
            .symbolEffect(.pulse, isActive: statusColor(status) == .orange)
    }
}

struct ContentView: View {
    @EnvironmentObject var store: HarnessStore
    @State private var selectedTask: String?
    @State private var showNewTask = false

    var body: some View {
        NavigationSplitView {
            List(store.runs, selection: $store.selectedRun) { run in
                HStack(spacing: 8) {
                    Image(systemName: statusSymbol(run.displayStatus)).foregroundStyle(statusColor(run.displayStatus))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(run.command).lineLimit(2)
                        Text(run.id.prefix(15) + " · " + run.displayStatus)
                            .font(.caption.monospaced()).foregroundStyle(.secondary)
                    }
                }
                .tag(run.id)
            }
            .navigationSplitViewColumnWidth(min: 250, ideal: 290)
            .safeAreaInset(edge: .bottom) { ModelsPanel().padding(10) }
            .overlay {
                if store.runs.isEmpty {
                    ContentUnavailableView("No runs yet", systemImage: "sparkles",
                                           description: Text("Press New Task (⌘N) and describe what to build."))
                }
            }
        } content: {
            RunView(selectedTask: $selectedTask)
                .navigationSplitViewColumnWidth(min: 400, ideal: 480)
        } detail: {
            if let run = store.current {
                if let name = selectedTask, let task = run.tasks.first(where: { $0.name == name }) {
                    FileTabsView(runDir: store.runsDir.appendingPathComponent(run.id), tabs: [
                        ("Log", task.log), ("Result", task.result), ("Patch", task.patch), ("Prompt", task.prompt),
                    ])
                    .id(name)
                } else {
                    FileTabsView(runDir: store.runsDir.appendingPathComponent(run.id), tabs: [
                        ("Summary", run.report), ("Events", nil), ("Final patch", "final.patch"), ("Engine log", "engine.log"),
                    ], events: run.events, initialTab: run.report == nil ? 1 : 0)
                    .id("\(run.id)-\(run.report != nil)")
                }
            } else {
                ContentUnavailableView("Select a run", systemImage: "cpu")
            }
        }
        .navigationTitle(URL(fileURLWithPath: store.repoPath).lastPathComponent)
        .navigationSubtitle(store.repoPath)
        .toolbar {
            ToolbarItem {
                Menu {
                    ForEach(store.recentRepos, id: \.self) { path in
                        Button(path) { store.repoPath = path }
                    }
                    Divider()
                    Button("Choose Project…", action: store.chooseRepo)
                } label: {
                    Label("Project", systemImage: "folder")
                }
            }
            ToolbarItem {
                Button("New Task", systemImage: "plus") { showNewTask = true }
                    .keyboardShortcut("n")
                    .disabled(!store.engineInstalled)
            }
        }
        .sheet(isPresented: $showNewTask) { NewTaskSheet() }
        .onChange(of: store.selectedRun) { old, _ in
            if old != nil { selectedTask = nil }
        }
        .overlay(alignment: .bottom) {
            if let message = store.message {
                Text(message)
                    .font(.callout)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.regularMaterial, in: Capsule())
                    .padding(16)
                    .onTapGesture { store.message = nil }
            }
        }
    }
}

struct ModelsPanel: View {
    @EnvironmentObject var store: HarnessStore

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Models").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if store.providers.isEmpty {
                Text("Run build.sh to install the engine.").font(.caption).foregroundStyle(.secondary)
            }
            ForEach(store.providers) { provider in
                Toggle(isOn: Binding(get: { provider.enabled }, set: { _ in store.toggle(provider) })) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(provider.label).font(.callout)
                        Text("→ " + provider.fallback.joined(separator: " → "))
                            .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .toggleStyle(.switch)
                .controlSize(.mini)
            }
        }
        .padding(10)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct NewTaskSheet: View {
    @EnvironmentObject var store: HarnessStore
    @Environment(\.dismiss) private var dismiss
    @State private var command = ""
    @State private var rounds = 3
    @State private var paths: [String] = []
    @State private var typedPath = ""

    func addTyped() {
        let path = typedPath.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if !path.isEmpty && !paths.contains(path) { paths.append(path) }
        typedPath = ""
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("New Task").font(.title2.weight(.semibold))
            Text(store.repoPath).font(.caption.monospaced()).foregroundStyle(.secondary)
            TextEditor(text: $command)
                .font(.body)
                .frame(minHeight: 160)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                .overlay(alignment: .topLeading) {
                    if command.isEmpty {
                        Text("What should the agents build or fix?")
                            .foregroundStyle(.tertiary).padding(6).allowsHitTesting(false)
                    }
                }
            VStack(alignment: .leading, spacing: 6) {
                Text("Target files/folders").font(.callout.weight(.medium))
                Text(paths.isEmpty ? "Whole project. Add paths to limit what agents may change." : "Agents may only change these paths.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(paths, id: \.self) { path in
                    HStack {
                        Image(systemName: "doc").foregroundStyle(.secondary)
                        Text(path).font(.callout.monospaced())
                        Spacer()
                        Button("Remove", systemImage: "minus.circle") { paths.removeAll { $0 == path } }
                            .labelStyle(.iconOnly).buttonStyle(.borderless)
                    }
                }
                HStack {
                    TextField("src/app.py or docs/", text: $typedPath)
                        .textFieldStyle(.roundedBorder)
                        .font(.callout.monospaced())
                        .onSubmit(addTyped)
                    Button("Add", action: addTyped).disabled(typedPath.trimmingCharacters(in: .whitespaces).isEmpty)
                    Button("Browse…") {
                        for path in store.pickPaths() where !paths.contains(path) { paths.append(path) }
                    }
                }
            }
            Stepper("Max rounds: \(rounds)", value: $rounds, in: 1...5)
            Text("Claude plans and reviews, workers run in parallel on isolated worktrees, and models fall back on usage limits. Nothing touches your project until you press Apply.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Start") {
                    store.start(command: command.trimmingCharacters(in: .whitespacesAndNewlines), rounds: rounds, paths: paths)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 560)
    }
}

struct RunView: View {
    @EnvironmentObject var store: HarnessStore
    @Binding var selectedTask: String?
    @State private var confirmApply = false

    var body: some View {
        if let run = store.current {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 8) {
                    if run.isFinished {
                        CompletionBanner(run: run) { selectedTask = nil }
                    }
                    Text(run.command).font(.headline).textSelection(.enabled)
                    HStack(spacing: 12) {
                        StatusBadge(status: run.displayStatus)
                        Text("round \(run.round)/\(run.maxRounds)").font(.caption).foregroundStyle(.secondary)
                        TimelineView(.periodic(from: .now, by: 1)) { _ in
                            Text(formatDuration(run.started, run.ended)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if let scope = run.scope, !scope.isEmpty {
                        Label(scope.joined(separator: ", "), systemImage: "scope")
                            .font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(2)
                    }
                    if let text = run.error ?? run.summary {
                        Text(text).font(.callout).foregroundStyle(run.error != nil ? .red : .primary).textSelection(.enabled)
                    }
                    if let verify = run.verify {
                        Label("\(verify.command): \(verify.passed ? "passed" : "failed (exit \(verify.exit))") in round \(verify.round)",
                              systemImage: verify.passed ? "checkmark.seal.fill" : "xmark.seal.fill")
                            .font(.caption).foregroundStyle(verify.passed ? .green : .red)
                    }
                    if let feedback = run.feedback, run.status == "ready" {
                        Text("Unresolved: \(feedback)").font(.caption).foregroundStyle(.orange)
                    }
                    HStack {
                        if run.isActive {
                            Button("Stop", systemImage: "stop.fill") { store.engineAction("stop", run: run.id) }
                        }
                        if run.status == "ready" {
                            Button("Apply to project", systemImage: "arrow.down.doc") { confirmApply = true }
                                .buttonStyle(.borderedProminent)
                        }
                        if run.canRetry {
                            Button("Retry", systemImage: "arrow.clockwise") {
                                store.start(command: run.command, rounds: run.maxRounds, paths: run.scope ?? [])
                            }
                            .buttonStyle(.borderedProminent)
                            .help("Start a new run with the same command, scope, and rounds")
                        }
                        Button("Show in Finder", systemImage: "folder") {
                            store.reveal(store.runsDir.appendingPathComponent(run.id))
                        }
                        if !run.isActive {
                            Button("Clean worktrees", systemImage: "trash") { store.engineAction("cleanup", run: run.id) }
                        }
                    }
                    .controlSize(.small)
                }
                .padding(12)
                Divider()
                List(selection: $selectedTask) {
                    ForEach(run.tasks) { task in TaskRow(task: task).tag(task.name) }
                }
            }
            .onChange(of: run.report) { if run.report != nil { selectedTask = nil } }
            .confirmationDialog(
                "Apply \(run.finalFiles?.count ?? 0) changed files to \(URL(fileURLWithPath: store.repoPath).lastPathComponent)?",
                isPresented: $confirmApply
            ) {
                Button("Apply") { store.engineAction("apply", run: run.id) }
            } message: {
                Text((run.finalFiles ?? []).joined(separator: "\n"))
            }
        } else {
            ContentUnavailableView("No run selected", systemImage: "list.bullet.rectangle")
        }
    }
}

struct TaskRow: View {
    let task: TaskState

    var providerText: String {
        guard let provider = task.provider else { return task.role }
        return provider == task.role ? provider : "\(task.role) → \(provider)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(task.name).font(.system(.body, design: .monospaced).weight(.medium))
                Text(providerText)
                    .font(.caption)
                    .foregroundStyle(task.provider.map { $0 != task.role } ?? false ? .orange : .secondary)
                Spacer()
                StatusBadge(status: task.state)
            }
            HStack(spacing: 12) {
                // state.json only changes on events, so tick the clock locally while a task runs.
                TimelineView(.periodic(from: .now, by: 1)) { _ in
                    Label(formatDuration(task.started, task.ended), systemImage: "clock")
                }
                if let changed = task.changedFiles { Label("\(changed) files", systemImage: "doc") }
                if let owned = task.ownedPaths { Text(owned.joined(separator: ", ")).lineLimit(1) }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            let notes = task.attempts.filter { $0.status != "ok" }
            if !notes.isEmpty {
                Text(notes.map { "\($0.provider): \($0.reason ?? $0.status)" }.joined(separator: " · "))
                    .font(.caption2).foregroundStyle(.orange).lineLimit(2)
            }
            if let error = task.error {
                Text(error).font(.caption2).foregroundStyle(.red).lineLimit(3)
            }
        }
        .padding(.vertical, 3)
    }
}

struct CompletionBanner: View {
    let run: RunState
    let showSummary: () -> Void

    var detail: String {
        let files = run.finalFiles?.count ?? 0
        switch run.status {
        case "applied": return "\(files) files applied to the project"
        case "no-changes": return "No file changes"
        default: return "\(files) files ready to apply"
        }
    }

    var body: some View {
        let color: Color = run.allDone ? .green : .orange
        HStack(spacing: 10) {
            Image(systemName: run.allDone ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                .font(.title2).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 2) {
                Text(run.allDone ? "All done" : "Finished with unresolved work").font(.headline)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if run.report != nil {
                Button("Summary", systemImage: "doc.text", action: showSummary).controlSize(.small)
            }
        }
        .padding(10)
        .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Renders the summary report: headings, bullets, and inline Markdown per line.
struct MarkdownView: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(text.components(separatedBy: "\n").enumerated()), id: \.offset) { _, raw in
                let line = raw.trimmingCharacters(in: .whitespaces)
                if line.hasPrefix("#") {
                    inline(line.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces))
                        .font(line.hasPrefix("##") ? .headline : .title3.bold()).padding(.top, 6)
                } else if line.hasPrefix("- ") || line.hasPrefix("* ") {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("•")
                        inline(String(line.dropFirst(2)))
                    }
                    .padding(.leading, raw.prefix(while: { $0 == " " }).count >= 2 ? 16 : 0)
                } else if !line.isEmpty {
                    inline(raw)
                }
            }
        }
        .textSelection(.enabled)
    }

    func inline(_ s: String) -> Text {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return Text((try? AttributedString(markdown: s, options: options)) ?? AttributedString(s))
    }
}

struct FileTabsView: View {
    let runDir: URL
    let tabs: [(String, String?)]
    var events: [EventItem] = []
    @State private var tab: Int
    @State private var follow = true

    init(runDir: URL, tabs: [(String, String?)], events: [EventItem] = [], initialTab: Int = 0) {
        self.runDir = runDir
        self.tabs = tabs
        self.events = events
        _tab = State(initialValue: initialTab)
    }

    func content() -> String? {
        let (title, path) = tabs[tab]
        if title == "Events" {
            let f = DateFormatter()
            f.dateFormat = "HH:mm:ss"
            return events.map { "\(f.string(from: Date(timeIntervalSince1970: $0.t)))  \($0.msg)" }.joined(separator: "\n")
        }
        return path.flatMap { Files.tail(runDir.appendingPathComponent($0)) }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("", selection: $tab) {
                    ForEach(tabs.indices, id: \.self) { Text(tabs[$0].0).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden().fixedSize()
                Spacer(minLength: 8)
                Toggle("Follow", isOn: $follow).toggleStyle(.checkbox)
                if let path = tabs[tab].1 {
                    Button("Open", systemImage: "arrow.up.forward.square") {
                        NSWorkspace.shared.open(runDir.appendingPathComponent(path))
                    }
                    .labelStyle(.iconOnly).buttonStyle(.borderless).help("Open in default app")
                }
            }
            .padding(10)
            Divider()
            TimelineView(.periodic(from: .now, by: 1.5)) { _ in
                let text = content()
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            Group {
                                if tabs[tab].0 == "Summary", let text, !text.isEmpty {
                                    MarkdownView(text: text).font(.body)
                                } else {
                                    Text(text.map { $0.isEmpty ? "(empty)" : $0 } ?? "(not created yet)")
                                        .font(.system(size: 11, design: .monospaced))
                                        .foregroundStyle(text == nil ? .secondary : .primary)
                                        .textSelection(.enabled)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                            Color.clear.frame(height: 1).id("bottom")
                        }
                    }
                    .onChange(of: text) { if follow && tabs[tab].0 != "Summary" { proxy.scrollTo("bottom", anchor: .bottomLeading) } }
                    .onAppear { if follow && tabs[tab].0 != "Summary" { proxy.scrollTo("bottom", anchor: .bottomLeading) } }
                }
            }
        }
    }
}

struct MenuContent: View {
    @EnvironmentObject var store: HarnessStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if let run = store.current {
            Text("\(run.command.prefix(40)) — \(run.displayStatus)")
            Divider()
            ForEach(run.tasks.filter { $0.state == "running" }) { task in
                Text("\(task.name): \(task.provider ?? task.role)  \(formatDuration(task.started, nil))")
            }
        } else {
            Text("No harness runs")
        }
        Divider()
        Button("Open HarnessLoop") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        .keyboardShortcut("o")
        Button("Quit") { NSApp.terminate(nil) }.keyboardShortcut("q")
    }
}

@main
struct HarnessMonitorApp: App {
    @StateObject private var store = HarnessStore()

    var body: some Scene {
        WindowGroup("HarnessLoop", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 1200, minHeight: 640)
        }
        MenuBarExtra {
            MenuContent().environmentObject(store)
        } label: {
            Image(systemName: store.activeTaskCount > 0 ? "bolt.horizontal.circle.fill" : "cpu")
            if store.activeTaskCount > 0 { Text("\(store.activeTaskCount)") }
        }
    }
}
