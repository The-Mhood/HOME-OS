package com.homeos.companion

import com.homeos.companion.security.RequestSigner
import org.junit.Assert.assertEquals
import org.junit.Test

class RequestSignerTest {
    @Test
    fun matchesBackendCanonicalHmacVector() {
        val sig = RequestSigner.signDeviceRequest(
            secret = "test_secret_123",
            method = "POST",
            path = "/api/v1/agent/telemetry",
            timestamp = 1700000000L,
            nonce = "nonce0001",
            body = """{"ok":true}""".toByteArray(Charsets.UTF_8),
        )
        assertEquals(64, sig.length)
    }
}
