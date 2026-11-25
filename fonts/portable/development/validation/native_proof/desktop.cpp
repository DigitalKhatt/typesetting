// Native Qt desktop reading proof. Each glyph run reports the font that
// actually supplied its outlines, including platform font fallback.
#include <QtGui>
#include <stdexcept>
#ifdef Q_OS_WIN
#include <windows.h>
#endif

static QByteArray read(const QString& file) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly)) throw std::runtime_error(file.toStdString());
    return f.readAll();
}
static QByteArray digest(const QByteArray& bytes) {
    return QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex();
}
int main(int argc, char** argv) {
    QGuiApplication app(argc, argv);
    try {
        if (argc != 3) throw std::runtime_error("Usage: portable-reading-proof KIT OUTPUT");
#ifdef Q_OS_WIN
        if (GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "wine_get_version"))
            throw std::runtime_error("Windows application captures require a native Windows host");
        QString platform = "windows", plugin = "windows";
#elif defined(Q_OS_MACOS)
        QString platform = "apple", plugin = "cocoa";
#else
        throw std::runtime_error("Desktop captures require Windows or macOS");
        QString platform, plugin;
#endif
        if (app.platformName() != plugin) throw std::runtime_error("The native desktop platform plugin is required");
        QDir kit(argv[1]), out(argv[2]);
        const auto specBytes = read(kit.filePath("cases.json"));
        auto spec = QJsonDocument::fromJson(specBytes).object();
        const auto fontBytes = read(kit.filePath("font.ttf"));
        if (digest(fontBytes) != spec["fontSha256"].toString().toLatin1()) throw std::runtime_error("Font hash mismatch");
        auto families = QFontDatabase::applicationFontFamilies(QFontDatabase::addApplicationFontFromData(fontBytes));
        if (families.isEmpty()) throw std::runtime_error("Could not load portable font");
        QJsonArray results;
        QHash<QRawFont, bool> identity;
        for (const auto item : spec["cases"].toArray()) {
            auto c = item.toObject();
            QString id = c["id"].toString(), text = c["text"].toString();
            int size = c["pixels"].toInt(), weight = c["weight"].toInt(), guard = c["guard"].toInt();
            int width = c["width"].toInt(), height = c["height"].toInt();
            QFont font(families.first());
            font.setPixelSize(size);
            font.setVariableAxis(QFont::Tag("wght"), weight);
            font.setFeature(QFont::Tag("tjwd"), spec["tajweed"].toBool() ? 1 : 0);
            QTextOption option;
            option.setTextDirection(Qt::RightToLeft);
            option.setAlignment(Qt::AlignLeft | Qt::AlignAbsolute);
            option.setWrapMode(QTextOption::NoWrap);
            QTextLayout layout(text, font);
            layout.setTextOption(option);
            layout.beginLayout();
            auto line = layout.createLine();
            line.setLineWidth(width);
            layout.endLayout();
            int fallback = 0, missing = 0, glyphs = 0;
            for (const auto& run : layout.glyphRuns(0, -1, QTextLayout::RetrieveAll)) {
                const auto raw = run.rawFont();
                if (!identity.contains(raw))
                    identity.insert(raw, digest(raw.fontTable("glyf")) == spec["glyfSha256"].toString().toLatin1());
                const auto ids = run.glyphIndexes();
                glyphs += ids.size();
                if (!identity.value(raw)) fallback += ids.size();
                for (auto gid : ids) if (gid == 0) ++missing;
            }
            QImage image(width + 2*guard, height + 2*guard, QImage::Format_RGB32);
            image.fill(Qt::white);
            QPainter painter(&image);
            painter.setPen(Qt::black);
            painter.setRenderHint(QPainter::TextAntialiasing);
            layout.draw(&painter, QPointF(guard+width-c["inset"].toInt()-line.horizontalAdvance(),
                                          guard+c["baseline"].toInt()-line.ascent()));
            painter.end();
            if (!image.save(out.filePath(id+".png"))) throw std::runtime_error("Could not write PNG");
            results.append(QJsonObject{{"id",id},{"glyphs",glyphs},{"fallbackGlyphs",fallback},{"missingGlyphs",missing}});
        }
        QJsonObject manifest{{"platform",platform},{"os",QSysInfo::prettyProductName()},
            {"renderer","Qt "+QString(qVersion())+" / "+plugin},{"fontSha256",QString(digest(fontBytes))},
            {"kitSha256",QString(digest(specBytes))},{"samplesSha256",spec["samplesSha256"]},{"cases",results}};
        QSaveFile output(out.filePath("capture.json"));
        if (!output.open(QIODevice::WriteOnly) || output.write(QJsonDocument(manifest).toJson()) < 0 || !output.commit())
            throw std::runtime_error("Could not publish native capture manifest");
    } catch (const std::exception& error) {
        qCritical("%s", error.what());
        return 1;
    }
}
