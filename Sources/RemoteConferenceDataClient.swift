import CryptoKit
import Foundation

enum RemoteConferenceDataResult {
    case unchanged
    case updated(ConferenceDataset)
}

private struct ConferenceDataManifest: Decodable {
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

struct RemoteConferenceDataClient {
    let manifestURL: URL
    var session: URLSession = .shared

    static func refresh(currentRevision: String) async throws -> RemoteConferenceDataResult {
        guard let address = Bundle.main.object(forInfoDictionaryKey: "ConferenceDataManifestURL") as? String,
              let url = URL(string: address) else {
            throw ConferenceDataError.missingResource("ConferenceDataManifestURL")
        }
        let client = RemoteConferenceDataClient(manifestURL: url)
        guard let snapshot = try await client.fetchSnapshot(currentRevision: currentRevision) else {
            return .unchanged
        }
        let dataset = try ConferenceDataLoader.dataset(from: snapshot, origin: .remote)
        try ConferenceDataLoader.saveCache(snapshot)
        return .updated(dataset)
    }

    func fetchSnapshot(currentRevision: String) async throws -> ConferenceSnapshot? {
        guard manifestURL.scheme == "https", manifestURL.host != nil,
              manifestURL.user == nil, manifestURL.password == nil else {
            throw ConferenceDataError.invalidData("数据更新地址必须是 HTTPS")
        }
        let manifestData = try await request(manifestURL, limit: 16_384, refresh: true)
        let manifest = try JSONDecoder().decode(ConferenceDataManifest.self, from: manifestData)
        guard manifest.schemaVersion == 1,
              manifest.revision.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil,
              manifest.sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
              (1...20_000_000).contains(manifest.byteCount) else {
            throw ConferenceDataError.invalidData("数据清单无效")
        }
        // Restrict downloads to the same Pages origin and this feed's directory.
        let directory = manifestURL.deletingLastPathComponent().path + "/"
        guard let dataURL = URL(string: manifest.dataURL, relativeTo: manifestURL)?.absoluteURL,
              dataURL.scheme == "https", dataURL.host == manifestURL.host,
              dataURL.port == manifestURL.port, dataURL.user == nil, dataURL.password == nil,
              dataURL.standardized.path.hasPrefix(directory),
              dataURL.query == nil, dataURL.fragment == nil else {
            throw ConferenceDataError.invalidData("数据文件地址不属于当前数据源")
        }
        guard manifest.revision != currentRevision else { return nil }

        // The commit-addressed file and checksum prevent mixing two deployments.
        let payload = try await request(dataURL, limit: manifest.byteCount)
        let digest = SHA256.hash(data: payload).map { String(format: "%02x", $0) }.joined()
        guard payload.count == manifest.byteCount, digest == manifest.sha256 else {
            throw ConferenceDataError.invalidData("数据文件校验失败，保留原有数据")
        }
        let snapshot = try JSONDecoder().decode(ConferenceSnapshot.self, from: payload)
        guard snapshot.schemaVersion == 1, snapshot.revision == manifest.revision else {
            throw ConferenceDataError.invalidData("数据版本与清单不一致")
        }
        return snapshot
    }

    private func request(_ url: URL, limit: Int, refresh: Bool = false) async throws -> Data {
        var request = URLRequest(
            url: url,
            cachePolicy: refresh ? .reloadIgnoringLocalCacheData : .useProtocolCachePolicy,
            timeoutInterval: 30
        )
        request.setValue("ConferenceCountdown/3", forHTTPHeaderField: "User-Agent")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if refresh { request.setValue("no-cache", forHTTPHeaderField: "Cache-Control") }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse,
              response.statusCode == 200, response.url?.scheme == "https",
              response.url?.host == manifestURL.host, data.count <= limit else {
            throw ConferenceDataError.invalidServerResponse
        }
        return data
    }
}
