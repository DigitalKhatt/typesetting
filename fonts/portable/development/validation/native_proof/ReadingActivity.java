package org.digitalkhatt.proof;

import android.app.Activity;
import android.os.Bundle;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

public class ReadingActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        new Thread(() -> {
            Path files = getFilesDir().toPath();
            try {
                for (String name : new String[]{"font.ttf","cases.json"}) {
                    try (InputStream in = getAssets().open(name)) {
                        Files.copy(in, files.resolve(name), StandardCopyOption.REPLACE_EXISTING);
                    }
                }
                ReadingProof.render(files);
            } catch (Throwable e) {
                try { Files.write(files.resolve("error.txt"), ("ReadingProofError\n"+android.util.Log.getStackTraceString(e)).getBytes("UTF-8")); }
                catch (Exception ignored) { android.util.Log.e("ReadingProof","Cannot save error",e); }
            } finally { runOnUiThread(this::finish); }
        }).start();
    }
}
