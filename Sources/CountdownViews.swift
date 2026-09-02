import AppKit
import SwiftUI

private struct EventRow: View {
    @EnvironmentObject private var model: CountdownModel
    let event: CountdownEvent

    var body: some View {
        let isPast = model.isPast(event)

        HStack(alignment: .top, spacing: 12) {
            Image(systemName: event.symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(
                    isPast
                        ? Color.secondary
                        : (event.id == model.selectedTargetID
                            ? Color.accentColor
                            : (model.isPredicted(event) ? Color.orange : Color.secondary))
                )
                .frame(width: 24, height: 24)

            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(event.title)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(isPast ? Color.secondary : Color.primary)
                    if model.isPredicted(event) {
                        Text("预测")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(isPast ? Color.secondary : Color.orange)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(
                                (isPast ? Color.secondary : Color.orange).opacity(0.12),
                                in: Capsule()
                            )
                    }
                    Spacer()
                    Text(model.remainingText(for: event))
                        .font(.system(size: 13, weight: .bold, design: .rounded))
                        .foregroundStyle(
                            isPast
                                ? Color.secondary
                                : (model.isPredicted(event) ? Color.orange : Color.primary)
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

private struct ConferenceRowFramePreferenceKey: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]

    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, newValue in newValue })
    }
}

private final class ConferenceReorderSurfaceView: NSView {
    var onDragBegan: () -> Void = {}
    var onDragChanged: (CGFloat) -> Void = { _ in }
    var onDragEnded: () -> Void = {}

    private var startingWindowY: CGFloat?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }

    override func resetCursorRects() {
        addCursorRect(bounds, cursor: .openHand)
    }

    override func mouseDown(with event: NSEvent) {
        startingWindowY = event.locationInWindow.y
        NSCursor.closedHand.set()
        onDragBegan()
    }

    override func mouseDragged(with event: NSEvent) {
        guard let startingWindowY else { return }
        let downwardDistance = startingWindowY - event.locationInWindow.y
        onDragChanged(downwardDistance)
    }

    override func mouseUp(with event: NSEvent) {
        startingWindowY = nil
        NSCursor.openHand.set()
        onDragEnded()
    }
}

private struct ConferenceReorderSurface: NSViewRepresentable {
    let onDragBegan: () -> Void
    let onDragChanged: (CGFloat) -> Void
    let onDragEnded: () -> Void

    func makeNSView(context: Context) -> ConferenceReorderSurfaceView {
        let view = ConferenceReorderSurfaceView()
        configure(view)
        return view
    }

    func updateNSView(_ view: ConferenceReorderSurfaceView, context: Context) {
        configure(view)
    }

    private func configure(_ view: ConferenceReorderSurfaceView) {
        view.onDragBegan = onDragBegan
        view.onDragChanged = onDragChanged
        view.onDragEnded = onDragEnded
    }
}

private struct ConferenceOptionsView: View {
    @EnvironmentObject private var model: CountdownModel
    @State private var draggedConferenceID: String?
    @State private var conferenceRowFrames: [String: CGRect] = [:]
    @State private var dragStartOrder: [String] = []
    @State private var dragStartRowCenters: [CGFloat] = []
    @State private var dragTargetIndex: Int?
    @State private var dragOffsetY: CGFloat = 0
    let onDone: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("选项")
                    .font(.title2.bold())
                Text("自动顺序按各会议的下一个里程碑；拖动整行可自定义。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 0) {
                ForEach(model.conferencesInConfiguredOrder) { conference in
                    HStack(spacing: 10) {
                        Toggle(
                            "",
                            isOn: Binding(
                                get: { model.isConferenceVisible(conference.id) },
                                set: { model.setConferenceVisible(conference.id, visible: $0) }
                            )
                        )
                        .labelsHidden()
                        .toggleStyle(.checkbox)
                        .disabled(!model.canHideConference(conference.id))

                        ZStack {
                            HStack(spacing: 10) {
                                Image(systemName: conference.symbol)
                                    .foregroundStyle(Color.accentColor)
                                    .frame(width: 22)

                                VStack(alignment: .leading, spacing: 2) {
                                    Text(conference.name)
                                        .font(.system(size: 13, weight: .semibold))
                                    Text(conference.subtitle)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }

                                Spacer(minLength: 6)

                                Image(systemName: "line.3.horizontal")
                                    .font(.system(size: 15, weight: .semibold))
                                    .foregroundStyle(
                                        draggedConferenceID == conference.id
                                            ? Color.accentColor
                                            : Color.secondary
                                    )
                                    .frame(width: 32, height: 32)
                            }

                            ConferenceReorderSurface(
                                onDragBegan: {
                                    beginDragging(conference.id)
                                },
                                onDragChanged: { downwardDistance in
                                    updateDragging(downwardDistance)
                                },
                                onDragEnded: {
                                    endDragging()
                                }
                            )
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .accessibilityHidden(true)
                        }
                        .frame(maxWidth: .infinity)
                        .contentShape(Rectangle())
                        .accessibilityLabel(conference.name + "，拖动调整顺序")
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 2)
                    .background {
                        GeometryReader { proxy in
                            RoundedRectangle(cornerRadius: 8, style: .continuous)
                                .fill(
                                    Color.accentColor.opacity(
                                        draggedConferenceID == conference.id ? 0.13 : 0
                                    )
                                )
                                .preference(
                                    key: ConferenceRowFramePreferenceKey.self,
                                    value: [
                                        conference.id: proxy.frame(
                                            in: .named("conference-options-rows")
                                        )
                                    ]
                                )
                        }
                    }
                    .scaleEffect(draggedConferenceID == conference.id ? 1.015 : 1)
                    .shadow(
                        color: Color.black.opacity(draggedConferenceID == conference.id ? 0.18 : 0),
                        radius: draggedConferenceID == conference.id ? 8 : 0,
                        y: draggedConferenceID == conference.id ? 3 : 0
                    )
                    .offset(y: rowOffset(for: conference.id))
                    .zIndex(draggedConferenceID == conference.id ? 1 : 0)

                    if conference.id != model.conferencesInConfiguredOrder.last?.id {
                        Divider()
                            .padding(.leading, 42)
                    }
                }
            }
            .background(Color.secondary.opacity(0.055))
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .stroke(Color.secondary.opacity(0.18), lineWidth: 1)
            }
            .coordinateSpace(name: "conference-options-rows")
            .onPreferenceChange(ConferenceRowFramePreferenceKey.self) {
                conferenceRowFrames = $0
            }

            HStack {
                Button("恢复按下一里程碑排序") {
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
        .frame(width: 470)
    }

    private func beginDragging(_ conferenceID: String) {
        let order = model.conferencesInConfiguredOrder.map(\.id)
        let measuredCenters = order.compactMap { conferenceRowFrames[$0]?.midY }

        draggedConferenceID = conferenceID
        dragStartOrder = order
        dragStartRowCenters = measuredCenters.count == order.count
            ? measuredCenters
            : order.indices.map { CGFloat($0) * 37 }
        dragTargetIndex = order.firstIndex(of: conferenceID)
        dragOffsetY = 0
    }

    private func updateDragging(_ downwardDistance: CGFloat) {
        guard let draggedConferenceID,
              let sourceIndex = dragStartOrder.firstIndex(of: draggedConferenceID),
              dragStartRowCenters.indices.contains(sourceIndex) else {
            return
        }

        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            dragOffsetY = downwardDistance
        }

        let draggedCenter = dragStartRowCenters[sourceIndex] + downwardDistance
        guard let targetIndex = dragStartRowCenters.indices.min(by: {
            abs(dragStartRowCenters[$0] - draggedCenter)
                < abs(dragStartRowCenters[$1] - draggedCenter)
        }) else {
            return
        }

        if dragTargetIndex != targetIndex {
            withAnimation(.interactiveSpring(response: 0.2, dampingFraction: 0.82)) {
                dragTargetIndex = targetIndex
            }
        }
    }

    private func endDragging() {
        guard let draggedConferenceID, let dragTargetIndex else {
            clearDragState()
            return
        }

        withAnimation(.interactiveSpring(response: 0.28, dampingFraction: 0.82)) {
            model.moveConference(draggedConferenceID, to: dragTargetIndex)
            clearDragState()
        }
    }

    private func rowOffset(for conferenceID: String) -> CGFloat {
        guard let draggedConferenceID,
              let sourceIndex = dragStartOrder.firstIndex(of: draggedConferenceID),
              let rowIndex = dragStartOrder.firstIndex(of: conferenceID),
              let dragTargetIndex,
              dragStartRowCenters.indices.contains(rowIndex) else {
            return 0
        }

        if conferenceID == draggedConferenceID {
            return dragOffsetY
        }

        if dragTargetIndex > sourceIndex,
           rowIndex > sourceIndex,
           rowIndex <= dragTargetIndex,
           dragStartRowCenters.indices.contains(rowIndex - 1) {
            return dragStartRowCenters[rowIndex - 1] - dragStartRowCenters[rowIndex]
        }

        if dragTargetIndex < sourceIndex,
           rowIndex >= dragTargetIndex,
           rowIndex < sourceIndex,
           dragStartRowCenters.indices.contains(rowIndex + 1) {
            return dragStartRowCenters[rowIndex + 1] - dragStartRowCenters[rowIndex]
        }

        return 0
    }

    private func clearDragState() {
        draggedConferenceID = nil
        dragStartOrder = []
        dragStartRowCenters = []
        dragTargetIndex = nil
        dragOffsetY = 0
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
