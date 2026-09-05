import AppKit
import SwiftUI

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

private struct ConferenceDrag {
    let id: String
    let order: [String]
    let centers: [CGFloat]
    let sourceIndex: Int
    var targetIndex: Int
    var offset: CGFloat = 0

    init?(id: String, order: [String], frames: [String: CGRect]) {
        guard let index = order.firstIndex(of: id) else { return nil }
        self.id = id
        self.order = order
        let measured = order.compactMap { frames[$0]?.midY }
        centers = measured.count == order.count ? measured : order.indices.map { CGFloat($0) * 37 }
        sourceIndex = index
        targetIndex = index
    }

    var nearestIndex: Int {
        let center = centers[sourceIndex] + offset
        return centers.indices.min { abs(centers[$0] - center) < abs(centers[$1] - center) }!
    }

    func offset(for conferenceID: String) -> CGFloat {
        if conferenceID == id { return offset }
        guard let index = order.firstIndex(of: conferenceID) else { return 0 }
        if sourceIndex < index, index <= targetIndex {
            return centers[index - 1] - centers[index]
        }
        if targetIndex <= index, index < sourceIndex {
            return centers[index + 1] - centers[index]
        }
        return 0
    }
}

struct ConferenceOptionsView: View {
    @EnvironmentObject private var model: CountdownModel
    @State private var drag: ConferenceDrag?
    @State private var conferenceRowFrames: [String: CGRect] = [:]
    let onDone: () -> Void

    var body: some View {
        let conferences = model.conferencesInConfiguredOrder

        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("选项")
                    .font(.title2.bold())
                Text("自动顺序按各会议的下一个里程碑；拖动整行可自定义。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 0) {
                ForEach(conferences) { conference in
                    let isDragging = drag?.id == conference.id

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
                                        isDragging ? Color.accentColor : Color.secondary
                                    )
                                    .frame(width: 32, height: 32)
                            }

                            ConferenceReorderSurface(
                                onDragBegan: {
                                    drag = ConferenceDrag(
                                        id: conference.id,
                                        order: conferences.map(\.id),
                                        frames: conferenceRowFrames
                                    )
                                },
                                onDragChanged: updateDragging,
                                onDragEnded: endDragging
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
                                .fill(Color.accentColor.opacity(isDragging ? 0.13 : 0))
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
                    .scaleEffect(isDragging ? 1.015 : 1)
                    .shadow(
                        color: Color.black.opacity(isDragging ? 0.18 : 0),
                        radius: isDragging ? 8 : 0,
                        y: isDragging ? 3 : 0
                    )
                    .offset(y: drag?.offset(for: conference.id) ?? 0)
                    .zIndex(isDragging ? 1 : 0)

                    if conference.id != conferences.last?.id {
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

                Button("完成", action: onDone)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 470)
    }

    private func updateDragging(_ downwardDistance: CGFloat) {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            drag?.offset = downwardDistance
        }

        if let drag, drag.targetIndex != drag.nearestIndex {
            withAnimation(.interactiveSpring(response: 0.2, dampingFraction: 0.82)) {
                self.drag?.targetIndex = drag.nearestIndex
            }
        }
    }

    private func endDragging() {
        guard let drag else { return }
        withAnimation(.interactiveSpring(response: 0.28, dampingFraction: 0.82)) {
            model.moveConference(drag.id, to: drag.targetIndex)
            self.drag = nil
        }
    }
}
