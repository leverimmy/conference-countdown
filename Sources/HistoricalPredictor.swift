import Foundation

enum HistoricalEventKey: String, Decodable {
    case abstractDeadline = "abstract_deadline"
    case paperDeadline = "paper_deadline"
    case commitmentDeadline = "commitment_deadline"
    case reviewRelease = "review_release"
    case rebuttalDeadline = "rebuttal_deadline"
    case finalDecision = "final_decision"
    case conferenceStart = "conference_start"
}

struct HistoricalDatePrediction {
    let date: Date
    let sampleCount: Int
    let uncertaintyDays: Int
}

struct HistoricalPredictor {
    private let conferences: [HistoricalConferenceFile]
    private let calendar: Calendar

    init(conferences: [HistoricalConferenceFile]) {
        self.conferences = conferences
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        self.calendar = calendar
    }

    func predict(
        conferenceID: String,
        eventKey: HistoricalEventKey,
        targetYear: Int,
        knownConferenceStart: Date?
    ) -> HistoricalDatePrediction? {
        guard let conference = conferences.first(where: { $0.id == conferenceID }) else {
            return nil
        }

        if eventKey == .conferenceStart {
            return predictConferenceStart(records: conference.records, targetYear: targetYear)
        }

        let leadDays = conference.records.compactMap { record -> Int? in
            guard let eventValue = record.value(for: eventKey),
                  let startValue = record.conferenceStart,
                  let eventDate = parseDate(eventValue),
                  let startDate = parseDate(startValue) else {
                return nil
            }
            return calendar.dateComponents([.day], from: eventDate, to: startDate).day
        }

        guard !leadDays.isEmpty else { return nil }

        let startPrediction: HistoricalDatePrediction?
        let targetStart: Date
        if let knownConferenceStart {
            targetStart = dateOnly(knownConferenceStart)
            startPrediction = nil
        } else if let predicted = predictConferenceStart(records: conference.records, targetYear: targetYear) {
            targetStart = predicted.date
            startPrediction = predicted
        } else {
            return nil
        }

        let medianLead = median(leadDays)
        guard let predictedDate = calendar.date(byAdding: .day, value: -medianLead, to: targetStart) else {
            return nil
        }
        let leadUncertainty = leadDays.map { abs($0 - medianLead) }.max() ?? 0
        let startUncertainty = startPrediction?.uncertaintyDays ?? 0

        return HistoricalDatePrediction(
            date: predictedDate,
            sampleCount: leadDays.count,
            uncertaintyDays: leadUncertainty + startUncertainty
        )
    }

    private func predictConferenceStart(
        records: [HistoricalRecord],
        targetYear: Int
    ) -> HistoricalDatePrediction? {
        guard let yearStart = makeDate(year: targetYear, month: 1, day: 1) else { return nil }

        let projectedOffsets = records.compactMap { record -> Int? in
            guard let value = record.conferenceStart,
                  let historicalDate = parseDate(value) else {
                return nil
            }
            let components = calendar.dateComponents([.month, .day], from: historicalDate)
            guard let month = components.month,
                  let day = components.day,
                  let projectedDate = makeDate(year: targetYear, month: month, day: day) else {
                return nil
            }
            return calendar.dateComponents([.day], from: yearStart, to: projectedDate).day
        }

        guard !projectedOffsets.isEmpty else { return nil }
        let medianOffset = median(projectedOffsets)
        guard let predictedDate = calendar.date(byAdding: .day, value: medianOffset, to: yearStart) else {
            return nil
        }
        let uncertainty = projectedOffsets.map { abs($0 - medianOffset) }.max() ?? 0

        return HistoricalDatePrediction(
            date: predictedDate,
            sampleCount: projectedOffsets.count,
            uncertaintyDays: uncertainty
        )
    }

    private func parseDate(_ value: String) -> Date? {
        let parts = value.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return makeDate(year: parts[0], month: parts[1], day: parts[2])
    }

    private func makeDate(year: Int, month: Int, day: Int) -> Date? {
        calendar.date(from: DateComponents(
            timeZone: calendar.timeZone,
            year: year,
            month: month,
            day: day,
            hour: 12
        ))
    }

    private func dateOnly(_ date: Date) -> Date {
        let components = calendar.dateComponents([.year, .month, .day], from: date)
        return makeDate(
            year: components.year!,
            month: components.month!,
            day: components.day!
        )!
    }

    private func median(_ values: [Int]) -> Int {
        let sorted = values.sorted()
        let midpoint = sorted.count / 2
        if sorted.count.isMultiple(of: 2) {
            return Int((Double(sorted[midpoint - 1]) + Double(sorted[midpoint])) / 2.0 + 0.5)
        }
        return sorted[midpoint]
    }
}
