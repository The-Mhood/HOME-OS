package com.homeos.companion.ui

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.homeos.companion.network.HomeOsHubService
import com.homeos.companion.security.KeystoreVault

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val vault = KeystoreVault(this)

        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                var enrolled by remember { mutableStateOf(vault.loadCredentials() != null) }
                var hubUrl by remember { mutableStateOf("https://192.168.1.10:8420") }
                var sessionId by remember { mutableStateOf("") }
                var pin by remember { mutableStateOf("") }

                Surface(modifier = Modifier.fillMaxSize()) {
                    Column(
                        modifier = Modifier
                            .padding(20.dp)
                            .verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(16.dp)
                    ) {
                        Text("HOME OS Companion", style = MaterialTheme.typography.headlineMedium)
                        Text(
                            "Zero-trust personal device bridge. Discovering this phone never grants private access; you control every permission below.",
                            style = MaterialTheme.typography.bodyMedium
                        )

                        if (!enrolled) {
                            OutlinedTextField(value = hubUrl, onValueChange = { hubUrl = it }, label = { Text("HOME OS Hub URL") }, modifier = Modifier.fillMaxWidth())
                            OutlinedTextField(value = sessionId, onValueChange = { sessionId = it }, label = { Text("Pairing Session ID") }, modifier = Modifier.fillMaxWidth())
                            OutlinedTextField(value = pin, onValueChange = { pin = it }, label = { Text("6-Digit PIN or QR Token") }, modifier = Modifier.fillMaxWidth())
                            Button(
                                onClick = {
                                    startForegroundService(Intent(this@MainActivity, HomeOsHubService::class.java))
                                },
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Text("Complete Secure Pairing")
                            }
                        } else {
                            Card(modifier = Modifier.fillMaxWidth()) {
                                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Text("Enrolled & Authenticated", style = MaterialTheme.typography.titleMedium)
                                    Button(onClick = {
                                        startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                                    }) {
                                        Text("Manage Android Notification Access")
                                    }
                                    Button(onClick = {
                                        startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                                    }) {
                                        Text("Optional: Accessibility Remote Input Settings")
                                    }
                                    OutlinedButton(
                                        onClick = {
                                            stopService(Intent(this@MainActivity, HomeOsHubService::class.java))
                                            vault.clear()
                                            enrolled = false
                                        }
                                    ) {
                                        Text("Emergency Disconnect & Revoke Vault")
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
