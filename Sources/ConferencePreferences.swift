import Foundation

struct ConferencePreferences {
    private(set) var order: [String]
    private(set) var visibleIDs: Set<String>
    private(set) var selectedTargetID: String
    private(set) var usesCustomOrder: Bool
    var remindersEnabled: Bool {
        didSet { defaults.set(remindersEnabled, forKey: Key.remindersEnabled) }
    }

    private var rememberedTargets: [String: String]
    private let defaults: UserDefaults

    private enum Key {
        static let selectedTarget = "selectedTarget"
        static let rememberedTargets = "selectedTargetsByConference"
        static let remindersEnabled = "remindersEnabled"
        static let order = "conferenceOrder"
        static let visible = "visibleConferences"
        static let migrated = "migratedLegacyWWWCountdownDefaults"
    }

    init(conferences: [Conference], defaults: UserDefaults = .standard) {
        Self.migrateLegacyDefaults(to: defaults)
        self.defaults = defaults
        let catalogIDs = conferences.map(\.id)
        let validIDs = Set(catalogIDs)
        let savedOrder = defaults.stringArray(forKey: Key.order) ?? []
        usesCustomOrder = savedOrder.contains(where: validIDs.contains)
        order = Self.mergedOrder(savedOrder, catalogIDs: catalogIDs)
        let savedVisible = Set(defaults.stringArray(forKey: Key.visible) ?? []).intersection(validIDs)
        let initialVisibleIDs = savedVisible.isEmpty ? validIDs : savedVisible
        visibleIDs = initialVisibleIDs
        remindersEnabled = defaults.bool(forKey: Key.remindersEnabled)
        rememberedTargets = defaults.dictionary(forKey: Key.rememberedTargets)?
            .compactMapValues { $0 as? String } ?? [:]
        rememberedTargets = Self.validTargets(rememberedTargets, in: conferences)

        let legacyTargets = ["abstract": "www.abstract", "paper": "www.paper", "conference": "www.conference"]
        let savedTarget = defaults.string(forKey: Key.selectedTarget).map { legacyTargets[$0] ?? $0 }
        let events = conferences.flatMap(\.events)
        if let event = events.first(where: { $0.id == savedTarget }),
           rememberedTargets[event.conferenceID] == nil {
            rememberedTargets[event.conferenceID] = event.id
        }
        let visible = conferences.filter { initialVisibleIDs.contains($0.id) }
        let initialEvent = visible.flatMap(\.events).first(where: { $0.id == savedTarget })
            ?? visible.flatMap(\.events).first(where: { $0.id == "www.paper" })
            ?? visible[0].defaultEvent
        selectedTargetID = initialEvent.id
        rememberedTargets[initialEvent.conferenceID] = initialEvent.id
        defaults.set(rememberedTargets, forKey: Key.rememberedTargets)
    }

    func target(for conference: Conference) -> CountdownEvent {
        conference.event(rememberedTargets[conference.id]) ?? conference.defaultEvent
    }

    mutating func select(_ event: CountdownEvent) {
        selectedTargetID = event.id
        rememberedTargets[event.conferenceID] = event.id
        saveSelection()
    }

    func canHide(_ id: String) -> Bool {
        !visibleIDs.contains(id) || visibleIDs.count > 1
    }

    mutating func setVisible(_ id: String, visible: Bool) {
        guard order.contains(id), visible || canHide(id) else { return }
        if visible {
            visibleIDs.insert(id)
        } else {
            visibleIDs.remove(id)
        }
        saveVisibleIDs()
    }

    mutating func move(_ id: String, to targetIndex: Int) {
        guard let sourceIndex = order.firstIndex(of: id) else { return }
        var updated = order
        updated.remove(at: sourceIndex)
        updated.insert(id, at: min(max(0, targetIndex), updated.count))
        guard updated != order else { return }
        order = updated
        usesCustomOrder = true
        defaults.set(order, forKey: Key.order)
    }

    mutating func sortByDate(_ chronologicalOrder: [String], reset: Bool = false) {
        if reset {
            usesCustomOrder = false
            defaults.removeObject(forKey: Key.order)
        }
        if !usesCustomOrder { order = chronologicalOrder }
    }

    mutating func update(conferences: [Conference]) {
        let showedAll = visibleIDs == Set(order)
        let catalogIDs = conferences.map(\.id)
        order = usesCustomOrder ? Self.mergedOrder(order, catalogIDs: catalogIDs) : catalogIDs
        visibleIDs = showedAll ? Set(catalogIDs) : visibleIDs.intersection(catalogIDs)
        if visibleIDs.isEmpty { visibleIDs = [order[0]] }
        rememberedTargets = Self.validTargets(rememberedTargets, in: conferences)

        let selected = conferences.flatMap(\.events).first { $0.id == selectedTargetID }
        if selected.map({ visibleIDs.contains($0.conferenceID) }) != true {
            let firstID = order.first(where: visibleIDs.contains)!
            let conference = conferences.first(where: { $0.id == firstID })!
            let replacement = target(for: conference)
            selectedTargetID = replacement.id
            rememberedTargets[conference.id] = replacement.id
        }
    }

    func save() {
        if usesCustomOrder { defaults.set(order, forKey: Key.order) }
        saveVisibleIDs()
        saveSelection()
    }

    private func saveSelection() {
        defaults.set(selectedTargetID, forKey: Key.selectedTarget)
        defaults.set(rememberedTargets, forKey: Key.rememberedTargets)
    }

    private func saveVisibleIDs() {
        defaults.set(order.filter(visibleIDs.contains), forKey: Key.visible)
    }

    private static func mergedOrder(_ saved: [String], catalogIDs: [String]) -> [String] {
        var remaining = Set(catalogIDs)
        return (saved + catalogIDs).filter { remaining.remove($0) != nil }
    }

    private static func validTargets(_ targets: [String: String], in conferences: [Conference]) -> [String: String] {
        targets.filter { conferenceID, eventID in
            conferences.first(where: { $0.id == conferenceID })?.event(eventID) != nil
        }
    }

    private static func migrateLegacyDefaults(to defaults: UserDefaults) {
        guard !defaults.bool(forKey: Key.migrated) else { return }
        if let legacy = UserDefaults(suiteName: "com.leverimmy.wwwcountdown") {
            for key in [Key.selectedTarget, Key.rememberedTargets, Key.remindersEnabled, Key.order, Key.visible]
                where defaults.object(forKey: key) == nil {
                if let value = legacy.object(forKey: key) { defaults.set(value, forKey: key) }
            }
        }
        defaults.set(true, forKey: Key.migrated)
    }
}
