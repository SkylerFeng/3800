package edu.aiia.glasses;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.view.View;
import android.webkit.GeolocationPermissions;
import android.webkit.CookieManager;
import android.webkit.HttpAuthHandler;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebMessage;
import android.webkit.WebMessagePort;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

public final class AssistantActivity extends Activity {
    private static final int MICROPHONE = 1;
    private static final int LOCATION = 2;
    private URI server;
    private WebView web;
    private TextView status;
    private final AssistantMic microphone = new AssistantMic();
    private final AtomicBoolean sampleQueued = new AtomicBoolean();
    private WebMessagePort micPort;
    private boolean foreground;
    private boolean amapOpened;
    private boolean returningFromAmap;
    private boolean micRequested;
    private int micGeneration;
    private GeolocationPermissions.Callback location;
    private String locationOrigin;
    private boolean authAttempted;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();

    @Override public void onCreate(Bundle savedState) {
        super.onCreate(savedState);
        try {
            server = Relay.hostUri(getIntent().getStringExtra("host"));
        } catch (IOException error) {
            finish();
            return;
        }
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        status = new TextView(this);
        status.setPadding(20, 16, 20, 16);
        status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        status.setText("正在连接电脑。");
        layout.addView(status);
        web = new WebView(this);
        layout.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(layout);

        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setUserAgentString(web.getSettings().getUserAgentString() + " 3800Mobile 3800NativeMic");
        web.setWebChromeClient(new BrowserPermissions());
        web.setWebViewClient(new Client());
        worker.execute(() -> {
            try {
                String cookie = Relay.webCookie(server.toString(), getIntent().getStringExtra("password"));
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    String page = server.resolve("/assistant").toString();
                    CookieManager.getInstance().setCookie(page, cookie, accepted -> {
                        if (isDestroyed()) return;
                        if (accepted) web.loadUrl(page);
                        else showStatus("无法建立助手登录会话。请返回重试。");
                    });
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (!isDestroyed()) showStatus(error instanceof IOException ? error.getMessage() : "连接电脑失败。");
                });
            }
        });
    }

    private int port(Uri uri) {
        return uri.getPort() < 0 ? 443 : uri.getPort();
    }

    private boolean sameServer(Uri uri) {
        int expectedPort = server.getPort() < 0 ? 443 : server.getPort();
        return "https".equalsIgnoreCase(uri.getScheme()) && server.getHost().equalsIgnoreCase(uri.getHost())
                && port(uri) == expectedPort && uri.getUserInfo() == null;
    }

    private WebResourceResponse errorResponse(String detail) {
        return new WebResourceResponse("text/plain", "UTF-8", 503, "Unavailable",
                Collections.singletonMap("Cache-Control", "no-store"),
                new ByteArrayInputStream(detail.getBytes(StandardCharsets.UTF_8)));
    }

    private void showStatus(String text) {
        status.setText(text);
        status.setVisibility(View.VISIBLE);
    }

    private final class Client extends WebViewClient {
        @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            Uri url = request.getUrl();
            if (!sameServer(url)) return errorResponse("连接超出了电脑站点。");
            if (!"/_3800/phone-snapshot".equals(url.getPath())
                    || !"GET".equals(request.getMethod()) || request.isForMainFrame()) return null;
            try {
                byte[] jpeg = Relay.snapshot(getIntent().getStringExtra("camera"));
                return new WebResourceResponse("image/jpeg", null, 200, "OK",
                        Collections.singletonMap("Cache-Control", "no-store"), new ByteArrayInputStream(jpeg));
            } catch (Exception error) {
                String message = error instanceof IOException ? error.getMessage() : "眼镜取图失败。";
                return errorResponse(message);
            }
        }

        @Override public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler,
                                                        String host, String realm) {
            String authority = server.getAuthority();
            if ((!server.getHost().equalsIgnoreCase(host) && !authority.equalsIgnoreCase(host))
                    || !"AIAA 3800".equals(realm) || authAttempted) {
                handler.cancel();
                showStatus("电脑登录失败。请返回检查访问口令。");
                return;
            }
            authAttempted = true;
            handler.proceed("3800", getIntent().getStringExtra("password"));
        }

        @Override public void onPageFinished(WebView view, String url) {
            if (sameServer(Uri.parse(url)) && "正在连接电脑。".contentEquals(status.getText())) {
                status.setVisibility(View.GONE);
            }
            if (sameServer(Uri.parse(url)) && "/assistant".equals(Uri.parse(url).getPath())) connectMicrophone();
        }

        @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            handler.cancel();
            showStatus("电脑证书校验失败。请检查 HTTPS 地址和手机信任的 3800 CA。");
        }

        @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame() && "正在连接电脑。".contentEquals(status.getText())) {
                showStatus("电脑连接失败。请返回测试电脑地址与手机当前网络。");
            }
        }

        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri url = request.getUrl();
            if (sameServer(url)) return false;
            if (!request.isForMainFrame()) return true;
            try {
                if ("intent".equals(url.getScheme()) && "openFeature".equals(url.getHost())) {
                    Intent link = Intent.parseUri(url.toString(), Intent.URI_INTENT_SCHEME);
                    Uri data = link.getData();
                    if (data != null && "amapuri".equals(data.getScheme())
                            && "openFeature".equals(data.getHost())
                            && "OnFootNavi".equals(data.getQueryParameter("featureName"))) {
                        startActivity(new Intent(Intent.ACTION_VIEW, data).setPackage("com.autonavi.minimap"));
                        amapOpened = true;
                    }
                } else if ("https".equals(url.getScheme()) && "uri.amap.com".equals(url.getHost())
                        && ("/search".equals(url.getPath()) || "/navigation".equals(url.getPath()))) {
                    startActivity(new Intent(Intent.ACTION_VIEW, url));
                }
            } catch (android.content.ActivityNotFoundException | java.net.URISyntaxException error) {
                showStatus("无法打开高德 App。可使用助手页中的高德网页入口。");
            }
            return true;
        }
    }

    private final class BrowserPermissions extends WebChromeClient {
        @Override public void onGeolocationPermissionsShowPrompt(String origin,
                                                                  GeolocationPermissions.Callback callback) {
            if (!sameServer(Uri.parse(origin))) {
                callback.invoke(origin, false, false);
            } else if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                    || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                callback.invoke(origin, true, false);
            } else if (location == null) {
                location = callback;
                locationOrigin = origin;
                requestPermissions(new String[] {Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION}, LOCATION);
            } else {
                callback.invoke(origin, false, false);
            }
        }
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(code, permissions, grants);
        if (code == MICROPHONE) {
            if (!micRequested) return;
            if (grants.length > 0 && grants[0] == PackageManager.PERMISSION_GRANTED) {
                startMic();
            } else {
                stopMic();
                sendMic("permission");
            }
        }
        if (code == LOCATION && location != null) {
            boolean allowed = false;
            for (int result : grants) allowed |= result == PackageManager.PERMISSION_GRANTED;
            location.invoke(locationOrigin, allowed, false);
            location = null;
        }
    }

    private void connectMicrophone() {
        stopMic();
        if (micPort != null) micPort.close();
        WebMessagePort[] ports = web.createWebMessageChannel();
        micPort = ports[0];
        micPort.setWebMessageCallback(new WebMessagePort.WebMessageCallback() {
            @Override public void onMessage(WebMessagePort port, WebMessage message) {
                if (port != micPort) return;
                if ("stop".equals(message.getData())) stopMic();
                if ("start".equals(message.getData())) {
                    if (!foreground || micRequested) return;
                    micRequested = true;
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                        startMic();
                    } else {
                        requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, MICROPHONE);
                    }
                }
            }
        });
        web.postWebMessage(new WebMessage("3800 microphone", new WebMessagePort[] {ports[1]}),
                Uri.parse(server.toString()));
    }

    private void sendMic(String data) {
        if (micPort != null) micPort.postMessage(new WebMessage(data));
    }

    private void startMic() {
        if (!foreground) {
            sendMic("paused");
            return;
        }
        int ticket = micGeneration;
        microphone.start(data -> {
            boolean sample = !"started".equals(data) && !"error".equals(data);
            if (sample && !sampleQueued.compareAndSet(false, true)) return;
            runOnUiThread(() -> {
                if (sample) sampleQueued.set(false);
                if (ticket != micGeneration || !foreground) return;
                if ("error".equals(data)) stopMic();
                sendMic(data);
            });
        });
    }

    private void stopMic() {
        micRequested = false;
        micGeneration++;
        microphone.stop();
    }

    @Override protected void onStart() {
        super.onStart();
        foreground = true;
        if (returningFromAmap) {
            returningFromAmap = false;
            sendMic("amap-return");
        }
    }

    @Override protected void onStop() {
        foreground = false;
        returningFromAmap = amapOpened;
        amapOpened = false;
        stopMic();
        sendMic(returningFromAmap ? "amap-pause" : "paused");
        super.onStop();
    }

    @Override protected void onDestroy() {
        microphone.close();
        if (micPort != null) {
            micPort.close();
            micPort = null;
        }
        worker.shutdownNow();
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
