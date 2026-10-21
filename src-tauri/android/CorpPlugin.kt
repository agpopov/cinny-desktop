package `in`.cinny.app

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import kotlin.random.Random
import android.content.Intent
import android.webkit.WebView
import app.tauri.annotation.Command
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import org.json.JSONObject

/** Retains a notification destination until the authenticated web UI consumes it. */
@TauriPlugin
class CorpPlugin(private val activity: Activity) : Plugin(activity) {
    private var pending: JSObject? = null

    override fun load(webView: WebView) { capture(activity.intent) }
    override fun onNewIntent(intent: Intent) { capture(intent) }

    private fun capture(intent: Intent?) {
        val raw = intent?.getStringExtra("cinny.notification.target") ?: return
        try {
            val target = JSONObject(raw)
            if (target.opt("roomId") is String) {
                pending = JSObject(target.toString())
                intent.removeExtra("cinny.notification.target")
            }
        } catch (_: Exception) { /* Ignore malformed or unrelated intents. */ }
    }

    @Command
    fun showMessage(invoke: Invoke) {
        try {
            val args = invoke.getArgs()
            val target = args.getJSONObject("target")
            require(target.opt("roomId") is String) { "Missing room destination" }
            val manager = activity.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channel = NotificationChannel("messages", "Сообщения", NotificationManager.IMPORTANCE_HIGH)
            channel.description = "Новые сообщения Cinny"
            channel.lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
            channel.setSound(null, null)
            manager.createNotificationChannel(channel)
            val id = Random.nextInt(1, Int.MAX_VALUE)
            val intent = Intent(activity, activity.javaClass)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra("cinny.notification.target", target.toString())
            val action = PendingIntent.getActivity(activity, id, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val body = args.getString("body")
            val notification = NotificationCompat.Builder(activity, "messages")
                .setSmallIcon(R.drawable.ic_stat_cinny)
                .setContentTitle(args.getString("title"))
                .setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setContentIntent(action)
                .setAutoCancel(true)
                .setSilent(true)
                .build()
            NotificationManagerCompat.from(activity).notify(id, notification)
            invoke.resolve()
        } catch (error: Exception) {
            invoke.reject(error.message ?: "Unable to show notification")
        }
    }

    @Command
    fun takeNotificationTarget(invoke: Invoke) {
        val target = pending ?: JSObject()
        pending = null
        invoke.resolve(target)
    }
}
