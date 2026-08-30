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
    @Published private(set) var selectedConferenceID: String
    @Published private(set) var selectedTargetID: String
    @Published private(set) var remindersEnabled: Bool
    @Published private(set) var launchAtLogin: Bool
    @Published private(set) var conferenceOrder: [String]
    @Published private(set) var visibleConferenceIDs: Set<String>
    @Published private(set) var conferences: [Conference]
    @Published private(set) var dataRevision: String
    @Published private(set) var dataOrigin: ConferenceDataOrigin
    @Published private(set) var isRefreshingData = false
    @Published var statusMessage: String?

    private let notificationCenter = UNUserNotificationCenter.current()
    private let notificationDelegate = NotificationDelegate()
    private var historicalPredictor: HistoricalPredictor
    private var clockTimer: AnyCancellable?
    private var dataRefreshTimer: AnyCancellable?
    private var usesCustomConferenceOrder: Bool
    private var selectedTargetByConference: [String: String]

    private enum DefaultsKey {
        static let selectedTarget = "selectedTarget"
        static let selectedTargetsByConference = "selectedTargetsByConference"
        static let remindersEnabled = "remindersEnabled"
        static let conferenceOrder = "conferenceOrder"
        static let visibleConferences = "visibleConferences"
        static let migratedLegacyDomain = "migratedLegacyWWWCountdownDefaults"
    }

    init() {
        Self.migrateLegacyDefaultsIfNeeded()

        let dataset: ConferenceDataset
        do {
            dataset = try ConferenceDataLoader.loadBestAvailable()
        } catch {
            fatalError("Unable to load bundled conference data: \(error.localizedDescription)")
        }

        let catalog = dataset.conferences
        let catalogIDs = catalog.map(\.id)
        let validIDs = Set(catalogIDs)
        let defaults = UserDefaults.standard

        conferences = catalog
        dataRevision = dataset.revision
        dataOrigin = dataset.origin
        historicalPredictor = HistoricalPredictor(conferences: dataset.histories)

        let savedOrder = defaults.stringArray(forKey: DefaultsKey.conferenceOrder)
        let knownSavedOrder = savedOrder?.filter(validIDs.contains) ?? []
        usesCustomConferenceOrder = savedOrder != nil && !knownSavedOrder.isEmpty
        conferenceOrder = usesCustomConferenceOrder
            ? knownSavedOrder + catalogIDs.filter { !knownSavedOrder.contains($0) }
            : catalogIDs

        let savedVisible = defaults
            .stringArray(forKey: DefaultsKey.visibleConferences)?
            .filter(validIDs.contains)
        let initialVisibleIDs = savedVisible.flatMap { $0.isEmpty ? nil : $0 } ?? catalogIDs
        let initialVisibleIDSet = Set(initialVisibleIDs)
        visibleConferenceIDs = initialVisibleIDSet

        let events = catalog.flatMap(\.events)
        let legacyTargets = [
            "abstract": "www.abstract",
            "paper": "www.paper",
            "conference": "www.conference"
        ]
        let savedTargetsByConference = defaults
            .dictionary(forKey: DefaultsKey.selectedTargetsByConference)?
            .compactMapValues { $0 as? String } ?? [:]
        var rememberedTargets: [String: String] = [:]
        for (conferenceID, eventID) in savedTargetsByConference {
            guard validIDs.contains(conferenceID),
                  events.contains(where: {
                      $0.id == eventID && $0.conferenceID == conferenceID
                  }) else { continue }
            rememberedTargets[conferenceID] = eventID
        }

        let savedTarget = defaults.string(forKey: DefaultsKey.selectedTarget)
        let migratedTarget = savedTarget.flatMap { legacyTargets[$0] ?? $0 }
        if let migratedTarget,
           let migratedEvent = events.first(where: { $0.id == migratedTarget }),
           rememberedTargets[migratedEvent.conferenceID] == nil {
            rememberedTargets[migratedEvent.conferenceID] = migratedEvent.id
        }

        let firstVisibleConference = catalog.first(where: { initialVisibleIDSet.contains($0.id) })!
        let initialEvent = events.first {
            $0.id == migratedTarget && initialVisibleIDSet.contains($0.conferenceID)
        } ?? events.first {
            $0.id == "www.paper" && initialVisibleIDSet.contains($0.conferenceID)
        } ?? firstVisibleConference.events.first(where: {
            $0.id == firstVisibleConference.defaultEventID
        }) ?? firstVisibleConference.events[0]

        selectedTargetID = initialEvent.id
        selectedConferenceID = initialEvent.conferenceID
        rememberedTargets[initialEvent.conferenceID] = initialEvent.id
        selectedTargetByConference = rememberedTargets
        defaults.set(rememberedTargets, forKey: DefaultsKey.selectedTargetsByConference)
        remindersEnabled = defaults.bool(forKey: DefaultsKey.remindersEnabled)
        launchAtLogin = SMAppService.mainApp.status == .enabled

        if !usesCustomConferenceOrder {
            conferenceOrder = conferencesByUpcomingDate.map(\.id)
        }

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

    var allEvents: [CountdownEvent] {
        conferences.flatMap(\.events)
    }

    var visibleEvents: [CountdownEvent] {
        visibleConferences.flatMap(\.events)
    }

    var conferencesInConfiguredOrder: [Conference] {
        let conferencesByID = Dictionary(uniqueKeysWithValues: conferences.map { ($0.id, $0) })
        return conferenceOrder.compactMap { conferencesByID[$0] }
    }

    var visibleConferences: [Conference] {
        conferencesInConfiguredOrder.filter { visibleConferenceIDs.contains($0.id) }
    }

    var selectedConference: Conference {
        conferences.first(where: { $0.id == selectedConferenceID }) ?? visibleConferences[0]
    }

    var conferencesByUpcomingDate: [Conference] {
        conferences.sorted { left, right in
            let leftDate = nextUpcomingDate(for: left)
            let rightDate = nextUpcomingDate(for: right)

            switch (leftDate, rightDate) {
            case let (leftDate?, rightDate?):
                if leftDate != rightDate {
                    return leftDate < rightDate
                }
                return left.shortName.localizedStandardCompare(right.shortName) == .orderedAscending
            case (_?, nil):
                return true
            case (nil, _?):
                return false
            case (nil, nil):
                return left.shortName.localizedStandardCompare(right.shortName) == .orderedAscending
            }
        }
    }

    var displayedEvents: [CountdownEvent] {
        selectedConference.events
    }

    var selectedEvent: CountdownEvent {
        allEvents.first(where: { $0.id == selectedTargetID }) ?? selectedConference.events[0]
    }

    var menuBarTitle: String {
        let event = selectedEvent
        guard let date = effectiveDate(for: event) else { return "\(event.compactTitle) · 待公布" }
        guard date > now else { return "\(event.compactTitle) · 已截止" }
        let approximation = isPredicted(event) ? "约" : ""
        return "\(event.compactTitle) · \(approximation)\(remainingDays(to: date))天"
    }

    var dataSourceDescription: String {
        let source: String
        switch dataOrigin {
        case .bundled:
            source = "App 内置数据"
        case .cached:
            source = "上次下载的数据"
        case .remote:
            source = "刚从 GitHub 更新的数据"
        }
        let revision = dataRevision == "bundled" ? "随 App 发布" : String(dataRevision.prefix(8))
        return "\(source) · \(revision)"
    }

    func selectConference(_ id: String) {
        guard visibleConferenceIDs.contains(id),
              let conference = conferences.first(where: { $0.id == id }) else { return }
        let targetID: String
        if let rememberedTargetID = selectedTargetByConference[conference.id],
           conference.events.contains(where: { $0.id == rememberedTargetID }) {
            targetID = rememberedTargetID
        } else {
            targetID = conference.defaultEventID
        }
        selectTarget(targetID)
    }

    func selectTarget(_ id: String) {
        guard let event = allEvents.first(where: { $0.id == id }) else { return }
        selectedTargetID = event.id
        selectedConferenceID = event.conferenceID
        selectedTargetByConference[event.conferenceID] = event.id
        UserDefaults.standard.set(event.id, forKey: DefaultsKey.selectedTarget)
        UserDefaults.standard.set(
            selectedTargetByConference,
            forKey: DefaultsKey.selectedTargetsByConference
        )
    }

    func isConferenceVisible(_ id: String) -> Bool {
        visibleConferenceIDs.contains(id)
    }

    func canHideConference(_ id: String) -> Bool {
        !visibleConferenceIDs.contains(id) || visibleConferenceIDs.count > 1
    }

    func setConferenceVisible(_ id: String, visible: Bool) {
        guard conferences.contains(where: { $0.id == id }) else { return }
        if visible {
            visibleConferenceIDs.insert(id)
        } else {
            guard visibleConferenceIDs.count > 1 else { return }
            visibleConferenceIDs.remove(id)
        }

        saveVisibleConferences()
        if !visibleConferenceIDs.contains(selectedConferenceID),
           let replacement = visibleConferences.first {
            selectConference(replacement.id)
        }
        if remindersEnabled {
            scheduleMilestoneNotifications()
        }
    }

    func moveConference(_ draggedID: String, over targetID: String) {
        guard draggedID != targetID,
              let sourceIndex = conferenceOrder.firstIndex(of: draggedID),
              let targetIndex = conferenceOrder.firstIndex(of: targetID) else { return }

        var updatedOrder = conferenceOrder
        let movingID = updatedOrder.remove(at: sourceIndex)
        guard let targetIndexAfterRemoval = updatedOrder.firstIndex(of: targetID) else { return }
        let insertionIndex = sourceIndex < targetIndex
            ? targetIndexAfterRemoval + 1
            : targetIndexAfterRemoval
        updatedOrder.insert(movingID, at: insertionIndex)
        conferenceOrder = updatedOrder
        usesCustomConferenceOrder = true
        saveConferenceOrder()
    }

    func resetConferenceOrderByDate() {
        usesCustomConferenceOrder = false
        conferenceOrder = conferencesByUpcomingDate.map(\.id)
        UserDefaults.standard.removeObject(forKey: DefaultsKey.conferenceOrder)
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

    func isUpcoming(_ event: CountdownEvent) -> Bool {
        guard let date = effectiveDate(for: event) else { return false }
        return date > now
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
            switch try await RemoteConferenceDataClient.refresh(currentRevision: dataRevision) {
            case .unchanged:
                if manual {
                    statusMessage = "会议数据已是最新。"
                }
            case let .updated(dataset):
                apply(dataset)
                statusMessage = "会议数据已更新至 \(String(dataset.revision.prefix(8)))。"
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
            remindersEnabled = false
            UserDefaults.standard.set(false, forKey: DefaultsKey.remindersEnabled)
            removeMilestoneNotifications()
            return
        }

        notificationCenter.requestAuthorization(options: [.alert, .sound]) { [weak self] granted, error in
            Task { @MainActor in
                guard let self else { return }
                if let error {
                    self.remindersEnabled = false
                    self.statusMessage = "无法开启通知：\(error.localizedDescription)"
                    return
                }
                self.remindersEnabled = granted
                UserDefaults.standard.set(granted, forKey: DefaultsKey.remindersEnabled)
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
        let oldConferenceIDs = Set(conferences.map(\.id))
        let wasShowingEveryConference = visibleConferenceIDs == oldConferenceIDs
        let previousSelectedTargetID = selectedTargetID

        conferences = dataset.conferences
        historicalPredictor = HistoricalPredictor(conferences: dataset.histories)
        dataRevision = dataset.revision
        dataOrigin = dataset.origin

        let newConferenceIDs = Set(conferences.map(\.id))
        let catalogOrder = conferences.map(\.id)
        let knownOrder = conferenceOrder.filter(newConferenceIDs.contains)
        conferenceOrder = usesCustomConferenceOrder
            ? knownOrder + catalogOrder.filter { !knownOrder.contains($0) }
            : catalogOrder

        if wasShowingEveryConference {
            visibleConferenceIDs = newConferenceIDs
        } else {
            visibleConferenceIDs.formIntersection(newConferenceIDs)
            if visibleConferenceIDs.isEmpty, let firstID = conferenceOrder.first {
                visibleConferenceIDs = [firstID]
            }
        }

        let validEvents = allEvents
        selectedTargetByConference = selectedTargetByConference.filter { conferenceID, eventID in
            newConferenceIDs.contains(conferenceID) && validEvents.contains(where: {
                $0.id == eventID && $0.conferenceID == conferenceID
            })
        }

        if let oldEvent = validEvents.first(where: { $0.id == previousSelectedTargetID }),
           visibleConferenceIDs.contains(oldEvent.conferenceID) {
            selectedConferenceID = oldEvent.conferenceID
            selectedTargetID = oldEvent.id
        } else if let firstConference = conferencesInConfiguredOrder.first(where: {
            visibleConferenceIDs.contains($0.id)
        }) {
            let rememberedID = selectedTargetByConference[firstConference.id]
            let replacement = firstConference.events.first(where: { $0.id == rememberedID })
                ?? firstConference.events.first(where: { $0.id == firstConference.defaultEventID })
                ?? firstConference.events[0]
            selectedConferenceID = firstConference.id
            selectedTargetID = replacement.id
            selectedTargetByConference[firstConference.id] = replacement.id
        }

        if !usesCustomConferenceOrder {
            conferenceOrder = conferencesByUpcomingDate.map(\.id)
        } else {
            saveConferenceOrder()
        }
        saveVisibleConferences()
        UserDefaults.standard.set(selectedTargetID, forKey: DefaultsKey.selectedTarget)
        UserDefaults.standard.set(
            selectedTargetByConference,
            forKey: DefaultsKey.selectedTargetsByConference
        )
        if remindersEnabled {
            scheduleMilestoneNotifications()
        }
    }

    private static func migrateLegacyDefaultsIfNeeded() {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: DefaultsKey.migratedLegacyDomain) else { return }
        if let legacy = UserDefaults(suiteName: "com.leverimmy.wwwcountdown") {
            let keys = [
                DefaultsKey.selectedTarget,
                DefaultsKey.selectedTargetsByConference,
                DefaultsKey.remindersEnabled,
                DefaultsKey.conferenceOrder,
                DefaultsKey.visibleConferences
            ]
            for key in keys where defaults.object(forKey: key) == nil {
                if let value = legacy.object(forKey: key) {
                    defaults.set(value, forKey: key)
                }
            }
        }
        defaults.set(true, forKey: DefaultsKey.migratedLegacyDomain)
    }

    private func remainingDays(to date: Date) -> Int {
        max(0, Calendar.current.dateComponents([.day], from: now, to: date).day ?? 0)
    }

    private func nextUpcomingDate(for conference: Conference) -> Date? {
        conference.events
            .compactMap(effectiveDate(for:))
            .filter { $0 > now }
            .min()
    }

    private func saveConferenceOrder() {
        UserDefaults.standard.set(conferenceOrder, forKey: DefaultsKey.conferenceOrder)
    }

    private func refreshAutomaticConferenceOrder() {
        guard !usesCustomConferenceOrder else { return }
        let chronologicalOrder = conferencesByUpcomingDate.map(\.id)
        if conferenceOrder != chronologicalOrder {
            conferenceOrder = chronologicalOrder
        }
    }

    private func saveVisibleConferences() {
        let orderedVisibleIDs = conferenceOrder.filter(visibleConferenceIDs.contains)
        UserDefaults.standard.set(orderedVisibleIDs, forKey: DefaultsKey.visibleConferences)
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

        for event in visibleEvents {
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

