package com.homeos.companion.security

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.security.MessageDigest
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

object RequestSigner {
    fun sha256Hex(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return digest.joinToString("") { "%02x".format(it) }
    }

    fun signDeviceRequest(
        secret: String,
        method: String,
        path: String,
        timestamp: Long,
        nonce: String,
        body: ByteArray = ByteArray(0),
    ): String {
        val bodySha = sha256Hex(body)
        val canonical = "${method.uppercase()}|$path|$timestamp|$nonce|$bodySha"
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return mac.doFinal(canonical.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    fun randomNonce(): String = UUID.randomUUID().toString().replace("-", "")
}

data class EnrollmentCredentials(
    val hubBaseUrl: String,
    val deviceId: String,
    val deviceToken: String,
    val hmacSecret: String,
    val tlsFingerprint: String,
)

class KeystoreVault(context: Context) {
    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context,
        "homeos_secure_vault",
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun saveCredentials(creds: EnrollmentCredentials) {
        prefs.edit()
            .putString("hub_url", creds.hubBaseUrl)
            .putString("device_id", creds.deviceId)
            .putString("device_token", creds.deviceToken)
            .putString("hmac_secret", creds.hmacSecret)
            .putString("tls_fp", creds.tlsFingerprint)
            .apply()
    }

    fun loadCredentials(): EnrollmentCredentials? {
        val hub = prefs.getString("hub_url", null) ?: return null
        val id = prefs.getString("device_id", null) ?: return null
        val tok = prefs.getString("device_token", null) ?: return null
        val sec = prefs.getString("hmac_secret", null) ?: return null
        val fp = prefs.getString("tls_fp", "") ?: ""
        return EnrollmentCredentials(hub, id, tok, sec, fp)
    }

    fun clear() {
        prefs.edit().clear().apply()
    }

    fun isScopeLocallyAllowed(scope: String): Boolean =
        prefs.getBoolean("perm_$scope", scope == "telemetry:read")

    fun setScopeLocallyAllowed(scope: String, allowed: Boolean) {
        prefs.edit().putBoolean("perm_$scope", allowed).apply()
    }
}
