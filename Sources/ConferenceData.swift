import Foundation

struct CountdownEvent: Identifiable {
    let id: String
    let conferenceID: String
    let title: String
    let compactTitle: String
    let date: Date?
    let dateLabel: String
    let localDateLabel: String
    let symbol: String
    let historicalKey: HistoricalEventKey?
    let targetYear: Int?
}

struct Conference: Identifiable {
    let id: String
    let edition: Int
    let name: String
    let shortName: String
    let subtitle: String
    let symbol: String
    let officialURL: String
    let timeZoneID: String
    let defaultEventID: String
    let events: [CountdownEvent]

    func event(_ id: String?) -> CountdownEvent? {
        events.first { $0.id == id }
    }

    var defaultEvent: CountdownEvent {
        // The loader requires a nonempty event list and a valid default ID.
        event(defaultEventID) ?? events[0]
    }
}

struct HistoricalConferenceFile: Codable {
    let schemaVersion: Int
    let lastVerified: String
    let id: String
    let records: [HistoricalRecord]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case lastVerified = "last_verified"
        case id
        case records
    }
}

struct HistoricalRecord: Codable {
    let year: Int
    let abstractDeadline: String?
    let paperDeadline: String?
    let commitmentDeadline: String?
    let reviewRelease: String?
    let rebuttalDeadline: String?
    let finalDecision: String?
    let conferenceStart: String?
    let conferenceEnd: String?
    let source: String
    let notes: String?

    enum CodingKeys: String, CodingKey {
        case year
        case abstractDeadline = "abstract_deadline"
        case paperDeadline = "paper_deadline"
        case commitmentDeadline = "commitment_deadline"
        case reviewRelease = "review_release"
        case rebuttalDeadline = "rebuttal_deadline"
        case finalDecision = "final_decision"
        case conferenceStart = "conference_start"
        case conferenceEnd = "conference_end"
        case source
        case notes
    }

    func value(for key: HistoricalEventKey) -> String? {
        switch key {
        case .abstractDeadline:
            return abstractDeadline
        case .paperDeadline:
            return paperDeadline
        case .commitmentDeadline:
            return commitmentDeadline
        case .reviewRelease:
            return reviewRelease
        case .rebuttalDeadline:
            return rebuttalDeadline
        case .finalDecision:
            return finalDecision
        case .conferenceStart:
            return conferenceStart
        }
    }
}

enum ConferenceDataOrigin {
    case bundled
    case cached
    case remote
}

struct ConferenceDataset {
    let revision: String
    let origin: ConferenceDataOrigin
    let conferences: [Conference]
    let histories: [HistoricalConferenceFile]
}

enum ConferenceDataError: LocalizedError {
    case missingResource(String)
    case invalidData(String)
    case invalidServerResponse

    var errorDescription: String? {
        switch self {
        case let .missingResource(name):
            return "缺少数据资源：\(name)"
        case let .invalidData(message):
            return "会议数据无效：\(message)"
        case .invalidServerResponse:
            return "远程数据服务返回了无效响应"
        }
    }
}

struct CatalogFile: Codable {
    let schemaVersion: Int
    let conferenceOrder: [String]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case conferenceOrder = "conference_order"
    }
}

struct CurrentEventFile: Codable {
    let id: String
    let title: String
    let compactTitle: String
    let at: String?
    let dateLabel: String
    let detailLabel: String
    let symbol: String
    let historicalKey: HistoricalEventKey?
    let targetYear: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case compactTitle = "compact_title"
        case at
        case dateLabel = "date_label"
        case detailLabel = "detail_label"
        case symbol
        case historicalKey = "historical_key"
        case targetYear = "target_year"
    }
}

struct CurrentConferenceFile: Codable {
    let schemaVersion: Int
    let lastVerified: String
    let id: String
    let edition: Int
    let name: String
    let shortName: String
    let subtitle: String
    let symbol: String
    let officialURL: String
    let timeZone: String
    let defaultEventID: String
    let events: [CurrentEventFile]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case lastVerified = "last_verified"
        case id
        case edition
        case name
        case shortName = "short_name"
        case subtitle
        case symbol
        case officialURL = "official_url"
        case timeZone = "time_zone"
        case defaultEventID = "default_event_id"
        case events
    }
}

struct ConferenceSnapshotEntry: Codable {
    let current: CurrentConferenceFile
    let history: HistoricalConferenceFile
}

struct ConferenceSnapshot: Codable {
    let schemaVersion: Int
    let revision: String
    let catalog: CatalogFile
    let conferences: [ConferenceSnapshotEntry]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case revision
        case catalog
        case conferences
    }
}

enum ConferenceDataLoader {
    private static let decoder = JSONDecoder()
    private static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func loadBestAvailable() throws -> ConferenceDataset {
        if let cacheURL = try? cacheURL(),
           let cachedData = try? Data(contentsOf: cacheURL),
           let snapshot = try? decoder.decode(ConferenceSnapshot.self, from: cachedData),
           let cached = try? dataset(from: snapshot, origin: .cached) {
            return cached
        }
        return try loadBundled()
    }

    static func loadBundled() throws -> ConferenceDataset {
        guard let resourceURL = Bundle.main.resourceURL else {
            throw ConferenceDataError.missingResource("Contents/Resources")
        }
        let root = resourceURL.appendingPathComponent("ConferenceData", isDirectory: true)
        let catalog: CatalogFile = try decodeFile(root.appendingPathComponent("catalog.json"))
        try validateCatalog(catalog)

        let entries = try catalog.conferenceOrder.map { conferenceID -> ConferenceSnapshotEntry in
            let directory = root.appendingPathComponent(conferenceID, isDirectory: true)
            return try ConferenceSnapshotEntry(
                current: decodeFile(directory.appendingPathComponent("current.json")),
                history: decodeFile(directory.appendingPathComponent("history.json"))
            )
        }
        return try dataset(
            from: ConferenceSnapshot(schemaVersion: 1, revision: "bundled", catalog: catalog, conferences: entries),
            origin: .bundled
        )
    }

    static func saveCache(_ snapshot: ConferenceSnapshot) throws {
        let data = try JSONEncoder().encode(snapshot)
        let url = try cacheURL()
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try data.write(to: url, options: .atomic)
    }

    private static func validateCatalog(_ catalog: CatalogFile) throws {
        guard catalog.schemaVersion == 1,
              !catalog.conferenceOrder.isEmpty,
              catalog.conferenceOrder.count <= 100,
              Set(catalog.conferenceOrder).count == catalog.conferenceOrder.count,
              catalog.conferenceOrder.allSatisfy({
                  $0.range(of: "^[a-z0-9]+(?:-[a-z0-9]+)*$", options: .regularExpression) != nil
              }) else {
            throw ConferenceDataError.invalidData("会议目录为空、过大或含无效/重复 ID")
        }
    }

    static func dataset(from snapshot: ConferenceSnapshot, origin: ConferenceDataOrigin) throws -> ConferenceDataset {
        guard snapshot.schemaVersion == 1 else {
            throw ConferenceDataError.invalidData("不支持的数据快照 schema")
        }
        let catalog = snapshot.catalog
        try validateCatalog(catalog)
        var currentByID: [String: CurrentConferenceFile] = [:]
        var historyByID: [String: HistoricalConferenceFile] = [:]
        for entry in snapshot.conferences {
            let current = entry.current
            let history = entry.history
            guard currentByID.updateValue(current, forKey: current.id) == nil else {
                throw ConferenceDataError.invalidData("current 中有重复会议：\(current.id)")
            }
            guard historyByID.updateValue(history, forKey: history.id) == nil else {
                throw ConferenceDataError.invalidData("history 中有重复会议：\(history.id)")
            }
        }
        guard Set(currentByID.keys) == Set(catalog.conferenceOrder),
              Set(historyByID.keys) == Set(catalog.conferenceOrder) else {
            throw ConferenceDataError.invalidData("catalog、current 和 history 的会议集合不一致")
        }

        let conferences = try catalog.conferenceOrder.map { try makeConference(currentByID[$0]!) }
        let orderedHistories = catalog.conferenceOrder.compactMap { historyByID[$0] }
        guard orderedHistories.allSatisfy({ $0.schemaVersion == 1 && !$0.records.isEmpty }) else {
            throw ConferenceDataError.invalidData("历史数据为空或 schema 不受支持")
        }
        return ConferenceDataset(
            revision: snapshot.revision,
            origin: origin,
            conferences: conferences,
            histories: orderedHistories
        )
    }

    private static func makeConference(_ current: CurrentConferenceFile) throws -> Conference {
        let id = current.id
        guard current.schemaVersion == 1 else {
            throw ConferenceDataError.invalidData("\(id) current.json 不可用")
        }
        guard TimeZone(identifier: current.timeZone) != nil else {
            throw ConferenceDataError.invalidData("\(id) 时区无效")
        }
        guard URL(string: current.officialURL)?.scheme == "https" else {
            throw ConferenceDataError.invalidData("\(id) 官网地址无效")
        }

        var eventIDs = Set<String>()
        let events = try current.events.map { source -> CountdownEvent in
            guard source.id.hasPrefix("\(id)."), eventIDs.insert(source.id).inserted else {
                throw ConferenceDataError.invalidData("事件 ID 重复或不属于 \(id)：\(source.id)")
            }
            let date: Date?
            if let value = source.at {
                guard let parsed = isoFormatter.date(from: value) else {
                    throw ConferenceDataError.invalidData("\(source.id) 的 at 不是 ISO 8601 时间")
                }
                date = parsed
            } else {
                guard source.historicalKey != nil, source.targetYear != nil else {
                    throw ConferenceDataError.invalidData("\(source.id) 无日期且没有预测字段")
                }
                date = nil
            }
            return CountdownEvent(
                id: source.id,
                conferenceID: id,
                title: source.title,
                compactTitle: source.compactTitle,
                date: date,
                dateLabel: source.dateLabel,
                localDateLabel: source.detailLabel,
                symbol: source.symbol,
                historicalKey: source.historicalKey,
                targetYear: source.targetYear
            )
        }
        guard eventIDs.contains(current.defaultEventID) else {
            throw ConferenceDataError.invalidData("\(id) 默认事件不存在")
        }
        return Conference(
            id: id,
            edition: current.edition,
            name: current.name,
            shortName: current.shortName,
            subtitle: current.subtitle,
            symbol: current.symbol,
            officialURL: current.officialURL,
            timeZoneID: current.timeZone,
            defaultEventID: current.defaultEventID,
            events: events
        )
    }

    private static func decodeFile<T: Decodable>(_ url: URL) throws -> T {
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw ConferenceDataError.missingResource(url.lastPathComponent)
        }
        return try decoder.decode(T.self, from: Data(contentsOf: url))
    }

    private static func cacheURL() throws -> URL {
        guard let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            throw ConferenceDataError.missingResource("Application Support")
        }
        return applicationSupport
            .appendingPathComponent("Conference Countdown", isDirectory: true)
            .appendingPathComponent("conference-data.json", isDirectory: false)
    }
}
