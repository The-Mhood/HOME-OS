package com.homeos.companion.services

import android.app.Notification
import android.app.RemoteInput
import android.content.Intent
import android.os.Bundle
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.homeos.companion.network.HomeOsHubService
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

class HomeOsNotificationListener : NotificationListenerService() {

    companion object {
        @Volatile
        var isConnected: Boolean = false
            private set

        private val replyActions = ConcurrentHashMap<String, Notification.Action>()

        fun sendInlineReply(actionId: String, replyText: String): Boolean {
            val action = replyActions[actionId] ?: return false
            val remoteInputs = action.remoteInputs ?: return false
            val fillIntent = Intent()
            val bundle = Bundle()
            for (ri in remoteInputs) {
                bundle.putCharSequence(ri.resultKey, replyText)
            }
            RemoteInput.addResultsToIntent(remoteInputs, fillIntent, bundle)
            action.actionIntent.send(null, 0, fillIntent)
            return true
        }
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        isConnected = true
    }

    override fun onListenerDisconnected() {
        isConnected = false
        replyActions.clear()
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.isOngoing) return
        val extras = sbn.notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: return
        val pkg = sbn.packageName

        val sourceType = when (pkg) {
            "com.whatsapp", "com.whatsapp.w4b" -> "whatsapp_notification"
            "org.telegram.messenger", "org.thoughtcrime.securesms", "com.google.android.apps.messaging" -> "messaging_notification"
            "com.google.android.dialer", "com.samsung.android.incallui" -> "call_event"
            else -> "android_notification"
        }

        var actionKey: String? = null
        sbn.notification.actions?.firstOrNull { !it.remoteInputs.isNullOrEmpty() }?.let { act ->
            actionKey = "reply:${sbn.key}"
            replyActions[actionKey!!] = act
        }

        val payload = JSONObject()
            .put("source_type", sourceType)
            .put("app_package", pkg)
            .put("app_name", pkg.substringAfterLast('.').replaceFirstChar { it.uppercase() })
            .put("sender", title.ifBlank { pkg })
            .put("title", title)
            .put("body_preview", text.take(500))
            .put("can_reply", actionKey != null)
            .put("reply_action_id", actionKey)
            .put("external_id", sbn.key)

        HomeOsHubService.publishCommunicationEvent(payload)
    }
}
