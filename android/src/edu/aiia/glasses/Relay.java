package edu.aiia.glasses;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import javax.net.ssl.SSLException;
import org.json.JSONObject;

final class Relay {
    private static final int MAX_JPEG = 1024 * 1024;
    private static final int MAX_RESPONSE = 6 * 1024 * 1024;
    private static final Pattern BOUNDARY = Pattern.compile(
            "(?:^|;)\\s*boundary\\s*=\\s*(?:\"([^\"]+)\"|([^;\\s]+))",
            Pattern.CASE_INSENSITIVE);

    static byte[] snapshot(String address) throws Exception {
        // Only the eye-worn camera uses cleartext, on its local IPv4 network.
        URI uri = parseUri("http://" + address, "请填写眼镜的局域网 IPv4 地址，可附端口。");
        String host = uri.getHost();
        if (host == null || !host.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}")
                || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                || !uri.getPath().isEmpty() || uri.getPort() == 0 || uri.getPort() > 65535) {
            throw new IOException("请填写眼镜的局域网 IPv4 地址，可附端口。");
        }
        String[] parts = host.split("\\.");
        int first = Integer.parseInt(parts[0]);
        int second = Integer.parseInt(parts[1]);
        for (String part : parts) {
            if (Integer.parseInt(part) > 255) throw new IOException("眼镜 IPv4 地址无效。");
        }
        if (!(first == 10 || first == 192 && second == 168
                || first == 172 && second >= 16 && second <= 31)) {
            throw new IOException("请使用眼镜热点内的局域网地址。");
        }
        HttpURLConnection connection = (HttpURLConnection) uri.resolve("/stream").toURL().openConnection(java.net.Proxy.NO_PROXY);
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(3500);
        connection.setReadTimeout(3500);
        try {
            if (connection.getResponseCode() != 200) throw new IOException("眼镜相机未返回视频。");
            try (InputStream stream = new BufferedInputStream(connection.getInputStream())) {
                return readSnapshot(stream, connection.getContentType());
            }
        } catch (java.net.SocketTimeoutException | java.net.SocketException error) {
            throw new IOException("连接眼镜失败。请确认眼镜已连手机热点，并填写当前 IP。", error);
        } finally {
            connection.disconnect();
        }
    }

    static byte[] readSnapshot(InputStream stream, String contentType) throws IOException {
        if (contentType == null || !contentType.toLowerCase(java.util.Locale.ROOT).startsWith("multipart/x-mixed-replace")) {
            throw new IOException("相机返回了不支持的视频格式。");
        }
        Matcher matcher = BOUNDARY.matcher(contentType);
        if (!matcher.find()) throw new IOException("相机视频缺少分帧标记。");
        String value = matcher.group(1) != null ? matcher.group(1) : matcher.group(2);
        String delimiter = "--" + value;
        long deadline = System.nanoTime() + 5_000_000_000L;
        String line = readLine(stream, deadline);
        if (line.isEmpty()) line = readLine(stream, deadline);
        if (!line.equals(delimiter)) throw new IOException("相机视频分帧标记无效。");
        int size = 0;
        for (int header = 0; header < 16; header++) {
            line = readLine(stream, deadline);
            if (line.isEmpty()) {
                if (size < 4 || size > MAX_JPEG) throw new IOException("相机图像大小无效。");
                byte[] jpeg = new byte[size];
                int offset = 0;
                while (offset < size && System.nanoTime() < deadline) {
                    int count = stream.read(jpeg, offset, size - offset);
                    if (count < 0) throw new EOFException("相机画面中途断开。");
                    offset += count;
                }
                if (offset != size) throw new IOException("相机读取超时。");
                if ((jpeg[0] & 255) != 255 || (jpeg[1] & 255) != 216) {
                    throw new IOException("相机帧不是 JPEG。");
                }
                return jpeg;
            }
            int colon = line.indexOf(':');
            if (colon > 0 && line.substring(0, colon).equalsIgnoreCase("Content-Length")) {
                String length = line.substring(colon + 1).trim();
                if (!length.matches("[0-9]{1,7}")) throw new IOException("相机帧长度无效。");
                size = Integer.parseInt(length);
            }
        }
        throw new IOException("相机分帧头部过长。");
    }

    private static String readLine(InputStream stream, long deadline) throws IOException {
        ByteArrayOutputStream line = new ByteArrayOutputStream();
        while (line.size() < 1024 && System.nanoTime() < deadline) {
            int value = stream.read();
            if (value < 0) throw new EOFException("相机连接已关闭。");
            if (value == '\n') return new String(line.toByteArray(), StandardCharsets.US_ASCII).trim();
            if (value != '\r') line.write(value);
        }
        throw new IOException("相机头部读取超时或过长。");
    }

    private static URI parseUri(String address, String error) throws IOException {
        try {
            return URI.create(address);
        } catch (IllegalArgumentException invalid) {
            throw new IOException(error, invalid);
        }
    }

    static URI hostUri(String address) throws IOException {
        URI uri = parseUri(address, "请输入有效的电脑 HTTPS 地址。");
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null
                || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                || !(uri.getPath().isEmpty() || uri.getPath().equals("/"))) {
            throw new IOException("电脑地址应为 HTTPS 站点地址，例如 https://电脑IP:8443。");
        }
        return uri;
    }

    static JSONObject host(String address, String password, byte[] jpeg, byte[] question,
                           boolean voice, boolean cloud) throws Exception {
        return hostRequest(address, password, jpeg, question, voice, cloud, false);
    }

    static String webCookie(String address, String password) throws Exception {
        return hostRequest(address, password, null, null, false, false, true).getString("cookie");
    }

    private static JSONObject hostRequest(String address, String password, byte[] jpeg, byte[] question,
                                          boolean voice, boolean cloud, boolean webView) throws Exception {
        URI uri = hostUri(address);
        if (password.isEmpty()) throw new IOException("请输入电脑网页访问口令。");
        String path = jpeg == null ? "/api/mobile/status"
                : "/api/mobile/ask?input=" + (voice ? "voice" : "text") + "&allow_cloud=" + cloud;
        HttpURLConnection connection = (HttpURLConnection) uri.resolve(path).toURL().openConnection(java.net.Proxy.NO_PROXY);
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(40000);
        String auth = Base64.getEncoder().encodeToString(("3800:" + password).getBytes(StandardCharsets.UTF_8));
        connection.setRequestProperty("Authorization", "Basic " + auth);
        if (webView) connection.setRequestProperty("X-3800-WebView", "1");
        try {
            if (jpeg != null) {
                connection.setRequestMethod("POST");
                connection.setRequestProperty("Content-Type", "application/octet-stream");
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(4 + jpeg.length + question.length);
                try (DataOutputStream output = new DataOutputStream(connection.getOutputStream())) {
                    output.writeInt(jpeg.length);
                    output.write(jpeg);
                    output.write(question);
                }
            }
            int status = connection.getResponseCode();
            if (status == 401) throw new IOException("电脑访问口令不正确。");
            if (status >= 300 && status < 400) throw new IOException("电脑返回了跳转，请核对 HTTPS 地址。");
            try (InputStream stream = status == 200 ? connection.getInputStream() : connection.getErrorStream()) {
                if (stream != null && connection.getContentType() != null
                        && connection.getContentType().startsWith("application/json")) {
                    JSONObject result = new JSONObject(readText(stream));
                    if (status == 200) {
                        if (webView) {
                            String cookie = connection.getHeaderField("Set-Cookie");
                            if (cookie == null || !cookie.startsWith("__Host-3800-mobile=")) {
                                throw new IOException("电脑尚未更新随身助手服务。");
                            }
                            result.put("cookie", cookie);
                        }
                        return result;
                    }
                    throw new IOException(result.optString("detail", "电脑请求失败（" + status + "）。"));
                }
            }
            throw new IOException("电脑返回了不可用的响应（" + status + "）。");
        } catch (SSLException error) {
            throw new IOException("电脑证书校验失败。请核对地址和手机已信任的 3800 CA。", error);
        } catch (java.net.SocketTimeoutException | java.net.SocketException | java.net.UnknownHostException error) {
            throw new IOException("电脑连接失败。请检查手机当前网络能否访问该 HTTPS 地址。", error);
        } finally {
            connection.disconnect();
        }
    }

    private static String readText(InputStream stream) throws IOException {
        ByteArrayOutputStream data = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        long deadline = System.nanoTime() + 45_000_000_000L;
        while (System.nanoTime() < deadline) {
            int count = stream.read(buffer);
            if (count < 0) return new String(data.toByteArray(), StandardCharsets.UTF_8);
            if (data.size() + count > MAX_RESPONSE) throw new IOException("电脑响应过大。");
            data.write(buffer, 0, count);
        }
        throw new IOException("读取电脑响应超时。");
    }
}
