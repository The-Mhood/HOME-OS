package com.homeos.companion.capabilities

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.provider.CallLog
import android.provider.Telephony
import android.telephony.SmsManager
import android.view.KeyEvent
import androidx.core.content.ContextCompat
import androidx.documentfile.provider.DocumentFile
import com.homeos.companion.BuildConfig
import com.homeos.companion.services.AuthorizedAccessibilityService
import com.homeos.companion.services.HomeOsNotificationListener
import org.json.JSONArray
import org.json.JSONObject

class AndroidDeviceController(private val context: Context) {

    var authorizedTreeUri: Uri? = null

    fun collectTelemetry(): JSONObject {
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val batteryPct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val isCharging = bm.isCharging

        val stat = StatFs(Environment.getDataDirectory().path)
        val totalGb = (stat.totalBytes / (1024.0 * 1024.0 * 1024.0))
        val freeGb = (stat.availableBytes / (1024.0 * 1024.0 * 1024.0))

        val attrs = JSONObject()
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("sdk_int", Build.VERSION.SDK_INT)
            .put("storage_total_gb", "%.1f".format(totalGb).toDouble())
            .put("storage_free_gb", "%.1f".format(freeGb).toDouble())
            .put("notification_listener_active", HomeOsNotificationListener.isConnected)
            .put("accessibility_active", AuthorizedAccessibilityService.instance != null)

        return JSONObject()
            .put("battery_level", batteryPct.coerceIn(0, 100))
            .put("is_charging", isCharging)
            .put("health_score", 100)
            .put("attributes", attrs)
            .put("capabilities", buildCapabilityManifest())
    }

    fun buildCapabilityManifest(): JSONArray {
        val cm = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val torchCamera = cm.cameraIdList.firstOrNull { id ->
            cm.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
        }
        val smsEligible = BuildConfig.ALLOW_RESTRICTED_TELEPHONY &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED
        val callLogEligible = BuildConfig.ALLOW_RESTRICTED_TELEPHONY &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED

        return JSONArray().apply {
            put(cap("telemetry.status", "telemetry", "Battery & System Status", true, null, "telemetry:read", false))
            put(cap(
                "notifications.sync", "communications", "Notification Synchronization",
                HomeOsNotificationListener.isConnected,
                if (HomeOsNotificationListener.isConnected) null else "Grant Notification Access to HOME OS Companion in Android Settings.",
                "notifications:read", false
            ))
            put(cap(
                "notifications.reply", "communications", "Notification Inline Reply (Messages / WhatsApp)",
                HomeOsNotificationListener.isConnected,
                if (HomeOsNotificationListener.isConnected) null else "Requires Notification Listener access.",
                "notifications:reply", false
            ))
            put(cap(
                "flashlight.set", "hardware", "Flashlight Torch",
                torchCamera != null,
                if (torchCamera != null) null else "Device has no rear camera flash unit.",
                "flashlight:control", false
            ))
            put(cap("media.control", "media", "Media Playback Controls", true, null, "media:control", false))
            put(cap("volume.set", "audio", "Stream Volume Control", true, null, "volume:control", false))
            put(cap("apps.launch", "applications", "Launch Application via Intent", true, null, "apps:launch", false))
            put(cap(
                "files.browse", "files", "Storage Access Framework Folder",
                authorizedTreeUri != null,
                if (authorizedTreeUri != null) null else "Select a shared folder in HOME OS Companion first.",
                "files:browse", false
            ))
            put(cap("screen.share", "screen", "MediaProjection Screen Sharing", true, null, "screen:view", true))
            put(cap(
                "input.remote", "input", "Authorized Accessibility Gesture Input",
                AuthorizedAccessibilityService.instance != null,
                if (AuthorizedAccessibilityService.instance != null) null else "Disabled by default; requires manual enablement in Android Accessibility Settings.",
                "input:remote", true
            ))
            put(cap(
                "sms.send", "communications", "Direct SMS Transmission",
                smsEligible,
                if (smsEligible) null else "Unavailable on Google Play flavor or SEND_SMS runtime permission not granted.",
                "sms:send", true
            ))
            put(cap(
                "calls.log", "communications", "Permitted Call History",
                callLogEligible,
                if (callLogEligible) null else "Unavailable on Google Play flavor or READ_CALL_LOG permission not granted.",
                "calls:read", false
            ))
        }
    }

    private fun cap(
        key: String,
        cat: String,
        name: String,
        supported: Boolean,
        reason: String?,
        perm: String,
        confirm: Boolean,
    ): JSONObject = JSONObject()
        .put("capability_key", key)
        .put("category", cat)
        .put("display_name", name)
        .put("supported", supported)
        .put("unavailable_reason", reason)
        .put("requires_permission", perm)
        .put("requires_confirmation", confirm)

    fun setFlashlight(enabled: Boolean): JSONObject {
        val cm = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val camId = cm.cameraIdList.firstOrNull { id ->
            cm.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
        } ?: throw IllegalStateException("No flash unit available")
        cm.setTorchMode(camId, enabled)
        return JSONObject().put("torch_enabled", enabled)
    }

    fun setMusicVolumePercent(percent: Int): JSONObject {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val target = ((percent.coerceIn(0, 100) / 100.0) * max).toInt()
        am.setStreamVolume(AudioManager.STREAM_MUSIC, target, 0)
        val actual = am.getStreamVolume(AudioManager.STREAM_MUSIC)
        return JSONObject().put("stream_volume", actual).put("max_volume", max)
    }

    fun dispatchMediaControl(action: String): JSONObject {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val keyCode = when (action.lowercase()) {
            "play" -> KeyEvent.KEYCODE_MEDIA_PLAY
            "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE
            "next" -> KeyEvent.KEYCODE_MEDIA_NEXT
            "previous" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
            "stop" -> KeyEvent.KEYCODE_MEDIA_STOP
            else -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
        }
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, keyCode))
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, keyCode))
        return JSONObject().put("dispatched_action", action)
    }

    fun launchPackage(packageName: String): JSONObject {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName)
            ?: throw IllegalArgumentException("Package '$packageName' has no launchable activity")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        return JSONObject().put("launched_package", packageName)
    }

    fun browseSafDirectory(): JSONObject {
        val uri = authorizedTreeUri ?: throw SecurityException("No Storage Access Framework directory authorized")
        val root = DocumentFile.fromTreeUri(context, uri)
            ?: throw IllegalStateException("Unable to open SAF DocumentFile tree")
        val entries = JSONArray()
        for (doc in root.listFiles()) {
            entries.put(
                JSONObject()
                    .put("name", doc.name ?: "unnamed")
                    .put("path", doc.uri.toString())
                    .put("is_dir", doc.isDirectory)
                    .put("size", doc.length())
            )
        }
        return JSONObject().put("current_path", uri.toString()).put("entries", entries)
    }

    fun sendSmsIfPermitted(recipient: String, body: String): JSONObject {
        if (!BuildConfig.ALLOW_RESTRICTED_TELEPHONY ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED
        ) {
            throw SecurityException("SEND_SMS is not permitted on this build/device")
        }
        val smsManager = context.getSystemService(SmsManager::class.java)
        smsManager.sendTextMessage(recipient, null, body, null, null)
        return JSONObject().put("recipient", recipient).put("sent", true)
    }

    fun readPermittedCallLog(limit: Int = 20): JSONObject {
        if (!BuildConfig.ALLOW_RESTRICTED_TELEPHONY ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED
        ) {
            throw SecurityException("READ_CALL_LOG is not permitted on this build/device")
        }
        val items = JSONArray()
        context.contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(CallLog.Calls.NUMBER, CallLog.Calls.TYPE, CallLog.Calls.DATE, CallLog.Calls.DURATION),
            null,
            null,
            "${CallLog.Calls.DATE} DESC"
        )?.use { cursor ->
            var count = 0
            while (cursor.moveToNext() && count < limit) {
                items.put(
                    JSONObject()
                        .put("number", cursor.getString(0) ?: "Unknown")
                        .put("type", cursor.getInt(1))
                        .put("date_ms", cursor.getLong(2))
                        .put("duration_sec", cursor.getLong(3))
                )
                count++
            }
        }
        return JSONObject().put("calls", items)
    }
}
