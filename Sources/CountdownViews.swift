import AppKit
import SwiftUI
import UniformTypeIdentifiers

private struct EventRow: View {
    @EnvironmentObject private var model: CountdownModel
    let event: CountdownEvent

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: event.symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(
                    event.id == model.selectedTargetID
                        ? Color.accentColor
                        : (model.isPredicted(event) ? Color.orange : Color.secondary)
                )
                .frame(width: 24, height: 24)

            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(event.title)
                        .font(.system(size: 13, weight: .semibold))
                    if model.isPredicted(event) {
                        Text("预测")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(Color.orange)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.orange.opacity(0.12), in: Capsule())
                    }
                    Spacer()
                    Text(model.remainingText(for: event))
                        .font(.system(size: 13, weight: .bold, design: .rounded))
                        .foregroundStyle(
                            model.isPredicted(event)
                                ? Color.orange
                                : (model.isUpcoming(event) ? Color.primary : Color.secondary)
                        )
                }
                Text(model.displayDateLabel(for: event))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(model.displayDetailLabel(for: event))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 5)
        .contentShape(Rectangle())
        .onTapGesture {
            model.selectTarget(event.id)
        }
    }
}

private struct ConferenceDropDelegate: DropDelegate {
    let targetID: String
    let model: CountdownModel
    @Binding var draggedConferenceID: String?

    func dropEntered(info: DropInfo) {
        guard let draggedConferenceID, draggedConferenceID != targetID else { return }
        model.moveConference(draggedConferenceID, over: targetID)
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        DropProposal(operation: .move)
    }

    func performDrop(info: DropInfo) -> Bool {
        draggedConferenceID = nil
        return true
    }
}

private struct ConferenceOptionsView: View {
    @EnvironmentObject private var model: CountdownModel
    @State private var draggedConferenceID: String?
    let onDone: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("选项")
                    .font(.title2.bold())
                Text("勾选要显示的会议；拖动右侧手柄调整 Tab 顺序。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            List {
                ForEach(model.conferencesInConfiguredOrder) { conference in
                    HStack(spacing: 10) {
                        Image(systemName: conference.symbol)
                            .foregroundStyle(Color.accentColor)
                            .frame(width: 22)

                        Toggle(
                            isOn: Binding(
                                get: { model.isConferenceVisible(conference.id) },
                                set: { model.setConferenceVisible(conference.id, visible: $0) }
                            )
                        ) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(conference.name)
                                    .font(.system(size: 13, weight: .semibold))
                                Text(conference.subtitle)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        .toggleStyle(.checkbox)
                        .disabled(!model.canHideConference(conference.id))

                        Spacer(minLength: 6)

                        Image(systemName: "line.3.horizontal")
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(.secondary)
                            .padding(6)
                            .contentShape(Rectangle())
                            .onDrag {
                                draggedConferenceID = conference.id
                                return NSItemProvider(object: conference.id as NSString)
                            }
                    }
                    .padding(.vertical, 4)
                    .onDrop(
                        of: [UTType.plainText],
                        delegate: ConferenceDropDelegate(
                            targetID: conference.id,
                            model: model,
                            draggedConferenceID: $draggedConferenceID
                        )
                    )
                }
            }
            .listStyle(.inset)

            HStack {
                Button("恢复按最近日期排序") {
                    model.resetConferenceOrderByDate()
                }

                Spacer()

                Button("完成") {
                    onDone()
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 470, height: 520)
    }
}

struct CountdownMenuView: View {
    @EnvironmentObject private var model: CountdownModel
    @State private var showingOptions = false

    var body: some View {
        if showingOptions {
            ConferenceOptionsView {
                showingOptions = false
            }
        } else {
            countdownContent
        }
    }

    private var countdownContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Text(model.selectedConference.name)
                            .font(.system(size: 17, weight: .bold, design: .rounded))
                        Button("官网") {
                            model.openOfficialWebsite()
                        }
                        .buttonStyle(.link)
                        .font(.caption)
                        .help("打开 \(model.selectedConference.shortName) 官网")
                    }
                    Text(model.selectedConference.subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Image(systemName: model.selectedConference.symbol)
                    .font(.system(size: 24))
                    .foregroundStyle(Color.accentColor)
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(model.visibleConferences) { conference in
                        let isSelected = conference.id == model.selectedConferenceID
                        Button {
                            model.selectConference(conference.id)
                        } label: {
                            Text(conference.shortName)
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(isSelected ? Color.white : Color.primary)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(
                                    isSelected ? Color.accentColor : Color.secondary.opacity(0.12),
                                    in: Capsule()
                                )
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(conference.name)
                    }
                }
                .padding(.horizontal, 1)
            }

            Divider()

            VStack(spacing: 3) {
                ForEach(model.displayedEvents) { event in
                    EventRow(event: event)
                    if event.id != model.displayedEvents.last?.id {
                        Divider().padding(.leading, 36)
                    }
                }
            }

            Text("橙色日期为历史预测，并非官方日期；点击项目可切换菜单栏目标。")
                .font(.caption2)
                .foregroundStyle(.tertiary)

            Divider()

            Toggle(
                "已显示会议的里程碑通知",
                isOn: Binding(
                    get: { model.remindersEnabled },
                    set: { model.setRemindersEnabled($0) }
                )
            )
            Toggle(
                "登录时自动启动",
                isOn: Binding(
                    get: { model.launchAtLogin },
                    set: { model.setLaunchAtLogin($0) }
                )
            )

            if let statusMessage = model.statusMessage {
                Text(statusMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack {
                Button {
                    showingOptions = true
                } label: {
                    Label("选项", systemImage: "gearshape")
                }
                .buttonStyle(.link)

                Spacer()

                Button {
                    Task {
                        await model.refreshConferenceData(manual: true)
                    }
                } label: {
                    Label(
                        model.isRefreshingData ? "正在更新" : "更新数据",
                        systemImage: model.isRefreshingData ? "arrow.triangle.2.circlepath" : "arrow.clockwise"
                    )
                }
                .buttonStyle(.link)
                .disabled(model.isRefreshingData)
                .help(model.dataSourceDescription)

                Spacer()

                Button("退出") {
                    model.quit()
                }
                .keyboardShortcut("q")
            }
            .controlSize(.small)
        }
        .padding(16)
        .frame(width: 400)
    }
}

