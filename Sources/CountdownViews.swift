import SwiftUI

private struct EventRow: View {
    @EnvironmentObject private var model: CountdownModel
    let event: CountdownEvent

    var body: some View {
        let isPast = model.isPast(event)
        let isPredicted = model.isPredicted(event)
        let forecastColor: Color = isPast ? .secondary : .orange
        let iconColor: Color = event.id == model.selectedTargetID ? .accentColor : (isPredicted ? .orange : .secondary)

        HStack(alignment: .top, spacing: 12) {
            Image(systemName: event.symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(isPast ? Color.secondary : iconColor)
                .frame(width: 24, height: 24)

            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(event.title)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(isPast ? Color.secondary : Color.primary)
                    if isPredicted {
                        Text("预测")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(forecastColor)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(forecastColor.opacity(0.12), in: Capsule())
                    }
                    Spacer()
                    Text(model.remainingText(for: event))
                        .font(.system(size: 13, weight: .bold, design: .rounded))
                        .foregroundStyle(
                            isPast ? Color.secondary : (isPredicted ? Color.orange : Color.primary)
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
        let conference = model.selectedConference
        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Text(conference.name)
                            .font(.system(size: 17, weight: .bold, design: .rounded))
                        Button("官网") {
                            model.openOfficialWebsite()
                        }
                        .buttonStyle(.link)
                        .font(.caption)
                        .help("打开 \(conference.shortName) 官网")
                    }
                    Text(conference.subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Image(systemName: conference.symbol)
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
                ForEach(conference.events) { event in
                    EventRow(event: event)
                    if event.id != conference.events.last?.id {
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
                    set: model.setRemindersEnabled
                )
            )
            Toggle(
                "登录时自动启动",
                isOn: Binding(
                    get: { model.launchAtLogin },
                    set: model.setLaunchAtLogin
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
