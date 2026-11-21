import SwiftUI

public struct CompanionRootView: View {
    @State private var hubUrl: String = "https://192.168.1.10:8420"
    @State private var pairingPin: String = ""
    @State private var isConnected: Bool = false
    @State private var showingFolderPicker: Bool = false

    public init() {}

    public var body: some View {
        NavigationStack {
            List {
                Section("Connection & Enrollment") {
                    TextField("HOME OS Hub URL", text: $hubUrl)
                    TextField("6-Digit Pairing PIN", text: $pairingPin)
                    Button(isConnected ? "Disconnect Session" : "Pair & Connect Securely") {
                        isConnected.toggle()
                    }
                }

                Section("Authorized Capabilities") {
                    Button("Choose Shared Folder (Security-Scoped Bookmark)") {
                        showingFolderPicker = true
                    }
                    Label("Apple Shortcuts & App Intents Ready", systemImage: "bolt.horizontal.circle")
                    Label("HomeKit & Matter Bridge Enabled", systemImage: "homekit")
                }

                Section("iOS Platform Privacy Boundaries") {
                    Text("SMS, Call History, WhatsApp databases, global notifications, and remote touch injection are intentionally unavailable due to Apple iOS sandbox protections.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("HOME OS")
        }
    }
}
