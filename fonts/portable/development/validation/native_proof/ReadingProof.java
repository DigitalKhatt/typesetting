package org.digitalkhatt.proof;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.fonts.Font;
import android.graphics.text.PositionedGlyphs;
import android.os.Build;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextDirectionHeuristics;
import android.text.TextPaint;
import android.text.TextShaper;
import org.json.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

public class ReadingProof {
    static String hash(ByteBuffer bytes) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        md.update(bytes);
        StringBuilder result = new StringBuilder();
        for (byte b : md.digest()) result.append(String.format(Locale.ROOT, "%02x", b & 255));
        return result.toString();
    }
    public static void render(Path dir) throws Exception {
        Path font = dir.resolve("font.ttf");
        byte[] specBytes = Files.readAllBytes(dir.resolve("cases.json"));
        JSONObject spec = new JSONObject(new String(specBytes, java.nio.charset.StandardCharsets.UTF_8));
        String expected = spec.getString("fontSha256");
        if (!hash(ByteBuffer.wrap(Files.readAllBytes(font))).equals(expected)) throw new IOException("Wrong font");
        JSONArray cases = spec.getJSONArray("cases"), results = new JSONArray();
        HashMap<Font, Boolean> identities = new HashMap<>();
        for (int n = 0; n < cases.length(); n++) {
            JSONObject c = cases.getJSONObject(n);
            String text = c.getString("text"), id = c.getString("id");
            int width = c.getInt("width"), height = c.getInt("height"), guard = c.getInt("guard");
            TextPaint p = new TextPaint(Paint.ANTI_ALIAS_FLAG | Paint.SUBPIXEL_TEXT_FLAG);
            p.setTypeface(new Typeface.Builder(font.toFile()).setFontVariationSettings("'wght' "+c.getInt("weight")).build());
            p.setTextLocale(Locale.forLanguageTag("ar"));
            p.setFontFeatureSettings("'tjwd' "+(spec.getBoolean("tajweed") ? "1" : "0"));
            p.setTextSize(c.getInt("pixels"));
            p.setColor(Color.BLACK);
            // Preserve paragraph bidi ordering for verse numbers within an Arabic line.
            int lineWidth = Math.max(width, (int)Math.ceil(Layout.getDesiredWidth(text, p)) + 2);
            StaticLayout layout = StaticLayout.Builder.obtain(text, 0, text.length(), p, lineWidth)
                .setTextDirection(TextDirectionHeuristics.RTL).setAlignment(Layout.Alignment.ALIGN_NORMAL)
                .setIncludePad(false).build();
            if (layout.getLineCount() != 1) throw new IOException("Unexpected line wrap: "+id);
            Bitmap bitmap = Bitmap.createBitmap(width+2*guard,height+2*guard,Bitmap.Config.ARGB_8888);
            Canvas canvas = new Canvas(bitmap);
            canvas.drawColor(Color.WHITE);
            canvas.translate(guard+width-c.getInt("inset")-layout.getLineRight(0),
                             guard+c.getInt("baseline")-layout.getLineBaseline(0));
            layout.draw(canvas);
            List<PositionedGlyphs> runs = new ArrayList<>();
            TextShaper.shapeText(text, 0, text.length(), TextDirectionHeuristics.RTL, p,
                                 (start, count, glyphs, paint) -> runs.add(glyphs));
            int missing=0, fallback=0, glyphCount=0;
            for (PositionedGlyphs glyphs : runs) {
                glyphCount += glyphs.glyphCount();
                for (int i=0; i<glyphs.glyphCount(); i++) {
                    Font face = glyphs.getFont(i);
                    if (!identities.containsKey(face)) {
                        ByteBuffer buffer = face.getBuffer().duplicate();
                        buffer.rewind();
                        identities.put(face, expected.equals(hash(buffer)));
                    }
                    if (!identities.get(face)) fallback++;
                    if (glyphs.getGlyphId(i)==0) missing++;
                }
            }
            try (OutputStream out = Files.newOutputStream(dir.resolve(id+".png"))) {
                if (!bitmap.compress(Bitmap.CompressFormat.PNG,100,out)) throw new IOException("PNG encoding failed");
            }
            bitmap.recycle();
            results.put(new JSONObject().put("id",id).put("glyphs",glyphCount)
                .put("fallbackGlyphs",fallback).put("missingGlyphs",missing));
        }
        JSONObject report = new JSONObject().put("platform","android").put("os",Build.FINGERPRINT)
            .put("renderer","Android StaticLayout / TextShaper API "+Build.VERSION.SDK_INT)
            .put("fontSha256",expected).put("kitSha256",hash(ByteBuffer.wrap(specBytes)))
            .put("samplesSha256",spec.getString("samplesSha256")).put("cases",results);
        Files.write(dir.resolve("capture.tmp"), report.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));
        Files.move(dir.resolve("capture.tmp"),dir.resolve("capture.json"),StandardCopyOption.ATOMIC_MOVE);
    }
}
