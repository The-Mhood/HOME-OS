package com.homeos.companion.network

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.IBinder
import com.homeos.companion.capabilities.AndroidDeviceController
import com.homeos.companion.security.KeystoreVault
import com.homeos.companion.security.RequestSigner
import com.homeos.companion.services.AuthorizedAccessibilityService
import com.homeos.companion.services.HomeOsNotificationListener
import kotlinx.coroutines.*
import okhttp3.*
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class HomeOsHubService : Service() {

    companion object {
        @Volatile
        private var activeWebSocket: WebSocket? = null

        fun publishCommunicationEvent(payload: JSONObject) {
            val frame = JSONObject()
                .put("type", "communication")
                .put("payload", payload)
            activeWebSocket?.send(frame.toString())
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var vault: KeystoreVault
    private lateinit var controller: AndroidDeviceController
    private val client = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    override fun onCreate() {
        super.onCreate()
        vault = KeystoreVault(this)
        controller = AndroidDeviceController(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val channelId = "homeos_hub_connection"
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(channelId, "HOME OS Connection", NotificationManager.IMPORTANCE_LOW)
        )
        startForeground(
            4201,
            Notification.Builder(this, channelId)
                .setContentTitle("HOME OS Connected")
                .setContentText("Authenticated encrypted session with local HOME OS hub.")
                .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
                .build()
        )

        scope.launch { connectLoop() }
        return START_STICKY
    }

    private suspend fun connectLoop() {
        var backoffMs = 1500L
        while (scope.isActive) {
            val creds = vault.loadCredentials() ?: break
            val ts = System.currentTimeMillis() / 1000L
            val nonce = RequestSigner.randomNonce()
            val sig = RequestSigner.signDeviceRequest(
                secret = creds.hmacSecret,
                method = "WS",
                path = "/api/v1/agent/ws",
                timestamp = ts,
                nonce = nonce,
            )
            val wsBase = creds.hubBaseUrl.replace("https://", "wss://").replace("http://", "ws://")
            val url = "$wsBase/api/v1/agent/ws?device_id=${enc(creds.deviceId)}&token=${enc(creds.deviceToken)}&timestamp=$ts&nonce=${enc(nonce)}&signature=$sig"

            val disconnected = CompletableDeferred<Unit>()
            val ws = client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    activeWebSocket = webSocket
                    backoffMs = 1500L
                    val telem = JSONObject()
                        .put("type", "telemetry")
                        .put("payload", controller.collectTelemetry())
                    webSocket.send(telem.toString())
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    val msg = JSONObject(text)
                    when (msg.optString("type")) {
                        "command.execute" -> handleCommand(webSocket, msg)
                        "session.terminated" -> {
                            webSocket.close(1000, "Hub terminated")
                            disconnected.complete(Unit)
                        }
                    }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    activeWebSocket = null
                    disconnected.complete(Unit)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    activeWebSocket = null
                    disconnected.complete(Unit)
                }
            })

            disconnected.await()
            ws.cancel()
            delay(backoffMs)
            backoffMs = (backoffMs * 2).coerceAtMost(20_000L)
        }
    }

    private fun handleCommand(ws: WebSocket, msg: JSONObject) {
        val cmdId = msg.getString("command_id")
        val cap = msg.getString("capability_key")
        val params = msg.optJSONObject("parameters") ?: JSONObject()

        val receipt = try {
            val resultObj = when (cap) {
                "telemetry.status" -> controller.collectTelemetry()
                "flashlight.set" -> controller.setFlashlight(params.getBoolean("enabled"))
                "volume.set" -> controller.setMusicVolumePercent(params.getInt("level"))
                "media.control" -> controller.dispatchMediaControl(params.getString("action"))
                "apps.launch" -> controller.launchPackage(params.getString("package_name"))
                "files.browse" -> controller.browseSafDirectory()
                "notifications.reply" -> {
                    val ok = HomeOsNotificationListener.sendInlineReply(
                        params.getString("reply_action_id"),
                        params.getString("message"),
                    )
                    if (!ok) throw IllegalStateException("Reply action expired or unavailable")
                    JSONObject().put("replied", true)
                }
                "input.remote" -> {
                    val svc = AuthorizedAccessibilityService.instance
                        ?: throw SecurityException("AccessibilityService is not enabled")
                    val ok = if (params.has("global_action")) {
                        svc.performSystemNavigation(params.getString("global_action"))
                    } else {
                        svc.dispatchTap(params.getDouble("x").toFloat(), params.getDouble("y").toFloat())
                    }
                    JSONObject().put("performed", ok)
                }
                "sms.send" -> controller.sendSmsIfPermitted(params.getString("to"), params.getString("body"))
                "calls.log" -> controller.readPermittedCallLog(params.optInt("limit", 20))
                else -> throw UnsupportedOperationException("Unsupported Android capability '$cap'")
            }

            JSONObject()
                .put("type", "command.receipt")
                .put("command_id", cmdId)
                .put("acknowledged", true)
                .put("state_verified", true)
                .put("verification_method", "android_platform_api")
                .put("result", resultObj)
        } catch (e: Exception) {
            JSONObject()
                .put("type", "command.receipt")
                .put("command_id", cmdId)
                .put("acknowledged", false)
                .put("state_verified", false)
                .put("error_code", "ANDROID_EXECUTION_ERROR")
                .put("error_message", e.message ?: "Execution failed")
        }
        ws.send(receipt.toString())
    }

    private fun enc(v: String): String = URLEncoder.encode(v, "UTF-8")

    override fun onDestroy() {
        scope.cancel()
        activeWebSocket?.close(1000, "Service stopped")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
