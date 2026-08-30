import SwiftUI

@main
private struct ConferenceCountdownApp: App {
    @StateObject private var model = CountdownModel()

    var body: some Scene {
        MenuBarExtra {
            CountdownMenuView()
                .environmentObject(model)
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "calendar.badge.clock")
                Text(model.menuBarTitle)
            }
        }
        .menuBarExtraStyle(.window)
    }
}

