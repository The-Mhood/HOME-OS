package com.homeos.companion.services

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.IBinder
import java.io.ByteArrayOutputStream

/**
 * Foreground MediaProjection screen sharing service.
 * Requires explicit Android system consent dialog on every session (Android 14+ policy).
 */
class ScreenProjectionService : Service() {

    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val channelId = "homeos_screen_share"
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(channelId, "HOME OS Screen Sharing", NotificationManager.IMPORTANCE_LOW)
        )
        val notification = Notification.Builder(this, channelId)
            .setContentTitle("HOME OS Screen Share Active")
            .setContentText("Your screen is currently shared with your authorized HOME OS desktop.")
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .build()

        startForeground(4202, notification)

        val resultCode = intent?.getIntExtra("resultCode", 0) ?: 0
        val resultData = intent?.getParcelableExtra("resultData", Intent::class.java)
        if (resultData != null) {
            val mpm = getSystemService(MediaProjectionManager::class.java)
            projection = mpm.getMediaProjection(resultCode, resultData)
            startVirtualCapture()
        }
        return START_NOT_STICKY
    }

    private fun startVirtualCapture() {
        val metrics = resources.displayMetrics
        val width = (metrics.widthPixels / 2).coerceAtLeast(360)
        val height = (metrics.heightPixels / 2).coerceAtLeast(640)
        imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        virtualDisplay = projection?.createVirtualDisplay(
            "HomeOsMirror",
            width,
            height,
            metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader?.surface,
            null,
            null,
        )
    }

    fun captureJpegSnapshot(): ByteArray? {
        val img = imageReader?.acquireLatestImage() ?: return null
        img.use { frame ->
            val plane = frame.planes[0]
            val bmp = Bitmap.createBitmap(frame.width, frame.height, Bitmap.Config.ARGB_8888)
            bmp.copyPixelsFromBuffer(plane.buffer)
            val out = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, 72, out)
            return out.toByteArray()
        }
    }

    override fun onDestroy() {
        virtualDisplay?.release()
        imageReader?.close()
        projection?.stop()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
