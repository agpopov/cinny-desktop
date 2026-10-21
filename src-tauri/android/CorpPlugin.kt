package `in`.cinny.app

import android.app.Activity
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
        val raw = intent?.getStringExtra("LocalNotficationObject") ?: return
        try {
            val target = JSONObject(raw).optJSONObject("extra")?.optJSONObject("target")
            if (target != null && target.has("roomId")) pending = JSObject(target.toString())
        } catch (_: Exception) { /* Ignore malformed or unrelated intents. */ }
    }

    @Command
    fun takeNotificationTarget(invoke: Invoke) {
        val target = pending ?: JSObject()
        pending = null
        invoke.resolve(target)
    }
}
