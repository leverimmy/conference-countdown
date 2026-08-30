import CryptoKit
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
}

struct HistoricalConferenceFile: Decodable {
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

struct HistoricalRecord: Decodable {
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
    case checksumMismatch

    var errorDescription: String? {
        switch self {
        case let .missingResource(name):
            return "缺少数据资源：\(name)"
        case let .invalidData(message):
            return "会议数据无效：\(message)"
        case .invalidServerResponse:
            return "远程数据服务返回了无效响应"
        case .checksumMismatch:
            return "远程数据校验失败"
        }
    }
}

private struct CatalogFile: Decodable {
    let schemaVersion: Int
    let conferenceOrder: [String]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case conferenceOrder = "conference_order"
    }
}

private struct CurrentEventFile: Decodable {
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

private struct CurrentConferenceFile: Decodable {
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

private struct FeedConferenceFile: Decodable {
    let current: CurrentConferenceFile
    let history: HistoricalConferenceFile
}

private struct ConferenceFeedFile: Decodable {
    let schemaVersion: Int
    let revision: String
    let catalog: CatalogFile
    let conferences: [FeedConferenceFile]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case revision
        case catalog
        case conferences
    }
}

private struct RemoteManifest: Decodable {
    let schemaVersion: Int
    let revision: String
    let dataURL: String
    let sha256: String
    let byteCount: Int

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case revision
        case dataURL = "data_url"
        case sha256
        case byteCount = "byte_count"
    }
}

enum RemoteConferenceDataResult {
    case unchanged
    case updated(ConferenceDataset)
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
           let cached = try? decodeFeed(cachedData, origin: .cached) {
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
        guard catalog.schemaVersion == 1 else {
            throw ConferenceDataError.invalidData("不支持的 catalog schema")
        }

        var currentFiles: [CurrentConferenceFile] = []
        var histories: [HistoricalConferenceFile] = []
        for conferenceID in catalog.conferenceOrder {
            let directory = root.appendingPathComponent(conferenceID, isDirectory: true)
            let current: CurrentConferenceFile = try decodeFile(directory.appendingPathComponent("current.json"))
            let history: HistoricalConferenceFile = try decodeFile(directory.appendingPathComponent("history.json"))
            currentFiles.append(current)
            histories.append(history)
        }
        return try makeDataset(
            revision: "bundled",
            origin: .bundled,
            catalog: catalog,
            currentFiles: currentFiles,
            histories: histories
        )
    }

    static func decodeFeed(_ data: Data, origin: ConferenceDataOrigin) throws -> ConferenceDataset {
        let feed = try decoder.decode(ConferenceFeedFile.self, from: data)
        guard feed.schemaVersion == 1 else {
            throw ConferenceDataError.invalidData("不支持的远程 Feed schema")
        }
        return try makeDataset(
            revision: feed.revision,
            origin: origin,
            catalog: feed.catalog,
            currentFiles: feed.conferences.map(\.current),
            histories: feed.conferences.map(\.history)
        )
    }

    static func saveCache(_ data: Data) throws {
        let url = try cacheURL()
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try data.write(to: url, options: .atomic)
    }

    private static func makeDataset(
        revision: String,
        origin: ConferenceDataOrigin,
        catalog: CatalogFile,
        currentFiles: [CurrentConferenceFile],
        histories: [HistoricalConferenceFile]
    ) throws -> ConferenceDataset {
        guard catalog.schemaVersion == 1,
              !catalog.conferenceOrder.isEmpty,
              Set(catalog.conferenceOrder).count == catalog.conferenceOrder.count else {
            throw ConferenceDataError.invalidData("会议目录为空或含重复 ID")
        }
        var currentByID: [String: CurrentConferenceFile] = [:]
        for current in currentFiles {
            guard currentByID.updateValue(current, forKey: current.id) == nil else {
                throw ConferenceDataError.invalidData("current 中有重复会议：\(current.id)")
            }
        }
        var historyByID: [String: HistoricalConferenceFile] = [:]
        for history in histories {
            guard historyByID.updateValue(history, forKey: history.id) == nil else {
                throw ConferenceDataError.invalidData("history 中有重复会议：\(history.id)")
            }
        }
        guard currentByID.count == catalog.conferenceOrder.count,
              historyByID.count == catalog.conferenceOrder.count,
              Set(currentByID.keys) == Set(catalog.conferenceOrder),
              Set(historyByID.keys) == Set(catalog.conferenceOrder) else {
            throw ConferenceDataError.invalidData("catalog、current 和 history 的会议集合不一致")
        }

        var globalEventIDs = Set<String>()
        let conferences = try catalog.conferenceOrder.map { conferenceID -> Conference in
            guard let current = currentByID[conferenceID], current.schemaVersion == 1 else {
                throw ConferenceDataError.invalidData("\(conferenceID) current.json 不可用")
            }
            guard TimeZone(identifier: current.timeZone) != nil else {
                throw ConferenceDataError.invalidData("\(conferenceID) 时区无效")
            }
            guard URL(string: current.officialURL)?.scheme == "https" else {
                throw ConferenceDataError.invalidData("\(conferenceID) 官网地址无效")
            }

            var localEventIDs = Set<String>()
            let events = try current.events.map { source -> CountdownEvent in
                guard source.id.hasPrefix("\(conferenceID)."),
                      localEventIDs.insert(source.id).inserted,
                      globalEventIDs.insert(source.id).inserted else {
                    throw ConferenceDataError.invalidData("事件 ID 重复或不属于 \(conferenceID)：\(source.id)")
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
                    conferenceID: conferenceID,
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
            guard localEventIDs.contains(current.defaultEventID) else {
                throw ConferenceDataError.invalidData("\(conferenceID) 默认事件不存在")
            }
            return Conference(
                id: current.id,
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

        let orderedHistories = catalog.conferenceOrder.compactMap { historyByID[$0] }
        guard orderedHistories.allSatisfy({ $0.schemaVersion == 1 && !$0.records.isEmpty }) else {
            throw ConferenceDataError.invalidData("历史数据为空或 schema 不受支持")
        }
        return ConferenceDataset(
            revision: revision,
            origin: origin,
            conferences: conferences,
            histories: orderedHistories
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

enum RemoteConferenceDataClient {
    static func refresh(currentRevision: String) async throws -> RemoteConferenceDataResult {
        guard let value = Bundle.main.object(forInfoDictionaryKey: "ConferenceDataManifestURL") as? String,
              let manifestURL = URL(string: value) else {
            throw ConferenceDataError.missingResource("ConferenceDataManifestURL")
        }

        let (manifestData, manifestResponse) = try await request(manifestURL)
        guard manifestResponse.statusCode == 200 else {
            throw ConferenceDataError.invalidServerResponse
        }
        let manifest = try JSONDecoder().decode(RemoteManifest.self, from: manifestData)
        guard manifest.schemaVersion == 1 else {
            throw ConferenceDataError.invalidData("不支持的远程 manifest schema")
        }
        if manifest.revision == currentRevision {
            return .unchanged
        }

        guard let dataURL = URL(string: manifest.dataURL, relativeTo: manifestURL)?.absoluteURL else {
            throw ConferenceDataError.invalidData("manifest 中的数据地址无效")
        }
        let (payload, payloadResponse) = try await request(dataURL)
        guard payloadResponse.statusCode == 200,
              payload.count == manifest.byteCount else {
            throw ConferenceDataError.invalidServerResponse
        }
        let checksum = SHA256.hash(data: payload).map { String(format: "%02x", $0) }.joined()
        guard checksum.caseInsensitiveCompare(manifest.sha256) == .orderedSame else {
            throw ConferenceDataError.checksumMismatch
        }
        let dataset = try ConferenceDataLoader.decodeFeed(payload, origin: .remote)
        guard dataset.revision == manifest.revision else {
            throw ConferenceDataError.invalidData("manifest 与 Feed revision 不一致")
        }
        try ConferenceDataLoader.saveCache(payload)
        return .updated(dataset)
    }

    private static func request(_ url: URL) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(
            url: url,
            cachePolicy: .reloadRevalidatingCacheData,
            timeoutInterval: 20
        )
        request.setValue("ConferenceCountdown/2", forHTTPHeaderField: "User-Agent")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw ConferenceDataError.invalidServerResponse
        }
        return (data, httpResponse)
    }
}
