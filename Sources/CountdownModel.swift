import AppKit
import Combine
import Foundation
import ServiceManagement
import UserNotifications

private final class NotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}

@MainActor
final class CountdownModel: ObservableObject {
    @Published private(set) var now = Date()
    @Published private(set) var launchAtLogin: Bool
    @Published private(set) var isRefreshingData = false
    @Published var statusMessage: String?
    @Published private var dataset: ConferenceDataset
    @Published private var preferences: ConferencePreferences

    private let notificationCenter = UNUserNotificationCenter.current()
    private let notificationDelegate = NotificationDelegate()
    private var historicalPredictor: HistoricalPredictor
    private var clockTimer: AnyCancellable?
    private var dataRefreshTimer: AnyCancellable?

    init() {
        let dataset: ConferenceDataset
        do {
            dataset = try ConferenceDataLoader.loadBestAvailable()
        } catch {
            fatalError("Unable to load bundled conference data: \(error.localizedDescription)")
        }

        self.dataset = dataset
        preferences = ConferencePreferences(conferences: dataset.conferences)
        historicalPredictor = HistoricalPredictor(conferences: dataset.histories)
        launchAtLogin = SMAppService.mainApp.status == .enabled
        refreshAutomaticConferenceOrder()

        notificationCenter.delegate = notificationDelegate
        clockTimer = Timer.publish(every: 60, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] date in
                self?.now = date
                self?.refreshAutomaticConferenceOrder()
            }
        dataRefreshTimer = Timer.publish(every: 24 * 60 * 60, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in
                Task { @MainActor in
                    await self?.refreshConferenceData(manual: false)
                }
            }

        if remindersEnabled {
            scheduleMilestoneNotifications()
        }
        Task { [weak self] in
            await self?.refreshConferenceData(manual: false)
        }
    }

    private var conferences: [Conference] { dataset.conferences }
    var selectedTargetID: String { preferences.selectedTargetID }
    var selectedConferenceID: String { selectedEvent.conferenceID }
    var remindersEnabled: Bool { preferences.remindersEnabled }

    private var allEvents: [CountdownEvent] {
        conferences.flatMap(\.events)
    }

    var conferencesInConfiguredOrder: [Conference] {
        let conferencesByID = Dictionary(uniqueKeysWithValues: conferences.map { ($0.id, $0) })
        return preferences.order.compactMap { conferencesByID[$0] }
    }

    var visibleConferences: [Conference] {
        conferencesInConfiguredOrder.filter { isConferenceVisible($0.id) }
    }

    var selectedConference: Conference {
        conferences.first(where: { $0.id == selectedConferenceID }) ?? visibleConferences[0]
    }

    private var conferencesByUpcomingDate: [Conference] {
        conferences.sorted { left, right in
            let leftDate = nextUpcomingDate(for: left)
            let rightDate = nextUpcomingDate(for: right)
            if leftDate == rightDate {
                return left.shortName.localizedStandardCompare(right.shortName) == .orderedAscending
            }
            guard let leftDate else { return false }
            guard let rightDate else { return true }
            return leftDate < rightDate
        }
    }

    var selectedEvent: CountdownEvent {
        allEvents.first(where: { $0.id == selectedTargetID }) ?? visibleConferences[0].defaultEvent
    }

    var menuBarTitle: String {
        let event = selectedEvent
        guard let date = effectiveDate(for: event) else { return "\(event.compactTitle) · 待公布" }
        guard date > now else { return "\(event.compactTitle) · 已截止" }
        let approximation = isPredicted(event) ? "约" : ""
        let days = Calendar.current.dateComponents([.day], from: now, to: date).day ?? 0
        return "\(event.compactTitle) · \(approximation)\(max(0, days))天"
    }

    var dataSourceDescription: String {
        let source: String
        switch dataset.origin {
        case .bundled:
            source = "App 内置数据"
        case .cached:
            source = "上次下载的数据"
        case .remote:
            source = "刚从 GitHub Pages 更新的数据"
        }
        let revision = dataset.revision == "bundled" ? "本地构建时的数据" : "main · \(shortRevision)"
        return "\(source) · \(revision)"
    }

    private var shortRevision: String { String(dataset.revision.prefix(8)) }

    func selectConference(_ id: String) {
        guard isConferenceVisible(id),
              let conference = conferences.first(where: { $0.id == id }) else { return }
        preferences.select(preferences.target(for: conference))
    }

    func selectTarget(_ id: String) {
        guard let event = allEvents.first(where: { $0.id == id }) else { return }
        preferences.select(event)
    }

    func isConferenceVisible(_ id: String) -> Bool {
        preferences.visibleIDs.contains(id)
    }

    func canHideConference(_ id: String) -> Bool {
        preferences.canHide(id)
    }

    func setConferenceVisible(_ id: String, visible: Bool) {
        guard conferences.contains(where: { $0.id == id }), visible || canHideConference(id) else { return }
        preferences.setVisible(id, visible: visible)
        if !isConferenceVisible(selectedConferenceID),
           let replacement = visibleConferences.first {
            selectConference(replacement.id)
        }
        if remindersEnabled {
            scheduleMilestoneNotifications()
        }
    }

    func moveConference(_ draggedID: String, to targetIndex: Int) {
        preferences.move(draggedID, to: targetIndex)
    }

    func resetConferenceOrderByDate() {
        preferences.sortByDate(conferencesByUpcomingDate.map(\.id), reset: true)
    }

    func remainingText(for event: CountdownEvent) -> String {
        guard let date = effectiveDate(for: event) else { return "待公布" }
        guard date > now else { return "已截止" }
        let components = Calendar.current.dateComponents([.day, .hour, .minute], from: now, to: date)
        let days = max(0, components.day ?? 0)
        let hours = max(0, components.hour ?? 0)
        let approximation = isPredicted(event) ? "约 " : ""
        if days == 0 {
            let minutes = max(0, components.minute ?? 0)
            return "\(approximation)\(hours) 小时 \(minutes) 分"
        }
        return "\(approximation)\(days) 天 \(hours) 小时"
    }

    func isPast(_ event: CountdownEvent) -> Bool {
        guard let date = effectiveDate(for: event) else { return false }
        return date <= now
    }

    func isPredicted(_ event: CountdownEvent) -> Bool {
        event.date == nil && prediction(for: event) != nil
    }

    func displayDateLabel(for event: CountdownEvent) -> String {
        guard event.date == nil, let prediction = prediction(for: event) else {
            return event.dateLabel
        }
        return "预测：\(Self.predictedDateFormatter.string(from: prediction.date))"
    }

    func displayDetailLabel(for event: CountdownEvent) -> String {
        guard event.date == nil, let prediction = prediction(for: event) else {
            return event.localDateLabel
        }
        let range = prediction.uncertaintyDays == 0
            ? "历史日期一致"
            : "历史最大偏差 ±\(prediction.uncertaintyDays) 天"
        return "官方待公布 · \(prediction.sampleCount) 届样本 · \(range)"
    }

    func refreshConferenceData(manual: Bool) async {
        guard !isRefreshingData else { return }
        isRefreshingData = true
        if manual {
            statusMessage = nil
        }
        defer { isRefreshingData = false }

        do {
            switch try await RemoteConferenceDataClient.refresh(currentRevision: dataset.revision) {
            case .unchanged:
                if manual {
                    statusMessage = "已是当前发布的数据（\(shortRevision)）。"
                }
            case let .updated(dataset):
                apply(dataset)
                statusMessage = "会议数据已更新至 \(shortRevision)。"
            }
        } catch {
            if manual {
                statusMessage = "更新失败，继续使用本地数据：\(error.localizedDescription)"
            }
        }
    }

    func setRemindersEnabled(_ enabled: Bool) {
        statusMessage = nil
        if !enabled {
            preferences.remindersEnabled = false
            removeMilestoneNotifications()
            return
        }

        notificationCenter.requestAuthorization(options: [.alert, .sound]) { [weak self] granted, error in
            Task { @MainActor in
                guard let self else { return }
                if let error {
                    self.preferences.remindersEnabled = false
                    self.statusMessage = "无法开启通知：\(error.localizedDescription)"
                    return
                }
                self.preferences.remindersEnabled = granted
                if granted {
                    self.scheduleMilestoneNotifications()
                    self.statusMessage = "已为官方日期和历史预测日期安排里程碑提醒。"
                } else {
                    self.statusMessage = "通知权限未开启，可在“系统设置 → 通知”中修改。"
                }
            }
        }
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        statusMessage = nil
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
            launchAtLogin = SMAppService.mainApp.status == .enabled
            if enabled && !launchAtLogin {
                statusMessage = "请在“系统设置 → 通用 → 登录项”中允许此 App。"
            }
        } catch {
            launchAtLogin = SMAppService.mainApp.status == .enabled
            statusMessage = "无法更改登录项：\(error.localizedDescription)"
        }
    }

    func openOfficialWebsite() {
        guard let url = URL(string: selectedConference.officialURL) else { return }
        NSWorkspace.shared.open(url)
    }

    func quit() {
        NSApplication.shared.terminate(nil)
    }

    private func apply(_ dataset: ConferenceDataset) {
        self.dataset = dataset
        historicalPredictor = HistoricalPredictor(conferences: dataset.histories)
        preferences.update(conferences: conferences)
        refreshAutomaticConferenceOrder()
        preferences.save()
        if remindersEnabled {
            scheduleMilestoneNotifications()
        }
    }

    private func nextUpcomingDate(for conference: Conference) -> Date? {
        conference.events
            .compactMap(effectiveDate(for:))
            .filter { $0 > now }
            .min()
    }

    private func refreshAutomaticConferenceOrder() {
        guard !preferences.usesCustomOrder else { return }
        let chronologicalOrder = conferencesByUpcomingDate.map(\.id)
        if preferences.order != chronologicalOrder {
            preferences.sortByDate(chronologicalOrder)
        }
    }

    private static let predictedDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy 年 M 月 d 日"
        return formatter
    }()

    private func prediction(for event: CountdownEvent) -> HistoricalDatePrediction? {
        guard event.date == nil,
              let historicalKey = event.historicalKey,
              let targetYear = event.targetYear else {
            return nil
        }

        let knownStart = conferences
            .first(where: { $0.id == event.conferenceID })?
            .events
            .first(where: { $0.id == "\(event.conferenceID).conference" })?
            .date
            .map { dateOnlyForPrediction($0, conferenceID: event.conferenceID) }

        return historicalPredictor.predict(
            conferenceID: event.conferenceID,
            eventKey: historicalKey,
            targetYear: targetYear,
            knownConferenceStart: knownStart
        )
    }

    private func effectiveDate(for event: CountdownEvent) -> Date? {
        if let officialDate = event.date {
            return officialDate
        }
        guard let prediction = prediction(for: event) else { return nil }
        return normalizedPredictionDate(prediction.date, for: event)
    }

    private func dateOnlyForPrediction(_ date: Date, conferenceID: String) -> Date {
        var sourceCalendar = Calendar(identifier: .gregorian)
        sourceCalendar.timeZone = conferenceTimeZone(for: conferenceID)
        let components = sourceCalendar.dateComponents([.year, .month, .day], from: date)

        var utcCalendar = Calendar(identifier: .gregorian)
        utcCalendar.timeZone = TimeZone(secondsFromGMT: 0)!
        return utcCalendar.date(from: DateComponents(
            timeZone: utcCalendar.timeZone,
            year: components.year,
            month: components.month,
            day: components.day,
            hour: 12
        ))!
    }

    private func normalizedPredictionDate(_ date: Date, for event: CountdownEvent) -> Date {
        var utcCalendar = Calendar(identifier: .gregorian)
        utcCalendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let components = utcCalendar.dateComponents([.year, .month, .day], from: date)

        let isConferenceStart = event.historicalKey == .conferenceStart
        let timeZone = isConferenceStart
            ? conferenceTimeZone(for: event.conferenceID)
            : TimeZone(secondsFromGMT: -12 * 60 * 60)!
        var targetCalendar = Calendar(identifier: .gregorian)
        targetCalendar.timeZone = timeZone
        return targetCalendar.date(from: DateComponents(
            timeZone: timeZone,
            year: components.year,
            month: components.month,
            day: components.day,
            hour: isConferenceStart ? 0 : 23,
            minute: isConferenceStart ? 0 : 59
        ))!
    }

    private func conferenceTimeZone(for conferenceID: String) -> TimeZone {
        guard let identifier = conferences.first(where: { $0.id == conferenceID })?.timeZoneID,
              let timeZone = TimeZone(identifier: identifier) else {
            return TimeZone(secondsFromGMT: 0)!
        }
        return timeZone
    }

    private func removeMilestoneNotifications() {
        notificationCenter.removeAllPendingNotificationRequests()
    }

    private func scheduleMilestoneNotifications() {
        removeMilestoneNotifications()
        let offsets = [60, 30, 14, 7, 3, 1, 0]
        let calendar = Calendar.current
        var scheduledRequests: [(date: Date, request: UNNotificationRequest)] = []

        for event in visibleConferences.flatMap(\.events) {
            guard let eventDate = effectiveDate(for: event) else { continue }
            let conferenceName = conferences.first(where: { $0.id == event.conferenceID })?.shortName ?? "会议"
            let predicted = isPredicted(event)

            for offset in offsets {
                guard let milestoneDay = calendar.date(byAdding: .day, value: -offset, to: eventDate) else { continue }
                var components = calendar.dateComponents([.year, .month, .day], from: milestoneDay)
                components.hour = 9
                components.minute = 0
                guard let reminderDate = calendar.date(from: components), reminderDate > Date() else { continue }

                let content = UNMutableNotificationContent()
                content.title = predicted ? "\(conferenceName) 预测提醒" : "\(conferenceName) 倒计时"
                let eventDescription = predicted ? "预测的\(event.title)" : event.title
                content.body = offset == 0
                    ? "今天是\(eventDescription)。"
                    : "距离\(eventDescription)还有 \(offset) 天。"
                content.sound = .default

                let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
                let request = UNNotificationRequest(
                    identifier: "conferencecountdown.\(event.id).\(offset)",
                    content: content,
                    trigger: trigger
                )
                scheduledRequests.append((reminderDate, request))
            }
        }

        // macOS keeps a limited number of pending notifications per app. Keep the nearest reminders.
        for item in scheduledRequests.sorted(by: { $0.date < $1.date }).prefix(60) {
            notificationCenter.add(item.request)
        }
    }
}
