package com.xpensimate.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ReceiptLong
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.UploadFile
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.xpensimate.app.data.Push
import com.xpensimate.app.data.QueueFlushWorker
import com.xpensimate.app.data.ServerConfig
import com.xpensimate.app.ui.AlertsScreen
import com.xpensimate.app.ui.AppCurrency
import com.xpensimate.app.ui.AlertsViewModel
import com.xpensimate.app.ui.BillsScreen
import com.xpensimate.app.ui.BillsViewModel
import com.xpensimate.app.ui.BudgetsScreen
import com.xpensimate.app.ui.BudgetsViewModel
import com.xpensimate.app.ui.CategoriesScreen
import com.xpensimate.app.ui.CategoriesViewModel
import com.xpensimate.app.ui.DashboardScreen
import com.xpensimate.app.ui.DashboardViewModel
import com.xpensimate.app.ui.XpensimateTheme
import com.xpensimate.app.ui.ImportScreen
import com.xpensimate.app.ui.ImportViewModel
import com.xpensimate.app.ui.LineDetailScreen
import com.xpensimate.app.ui.LineDetailViewModel
import com.xpensimate.app.ui.LoadingView
import com.xpensimate.app.ui.PersonLines
import com.xpensimate.app.ui.ReceiptDetailScreen
import com.xpensimate.app.ui.ReceiptDetailViewModel
import com.xpensimate.app.ui.ReceiptsScreen
import com.xpensimate.app.ui.ReceiptsViewModel
import com.xpensimate.app.ui.ReviewScreen
import com.xpensimate.app.ui.ReviewViewModel
import com.xpensimate.app.ui.SettingsScreen
import com.xpensimate.app.ui.SetupScreen
import com.xpensimate.app.ui.TransactionDetailScreen
import com.xpensimate.app.ui.TransactionDetailViewModel
import com.xpensimate.app.ui.TransactionsScreen
import com.xpensimate.app.ui.TransactionsViewModel
import com.xpensimate.app.ui.rememberViewModel
import kotlinx.coroutines.launch
import java.time.YearMonth

class MainActivity : ComponentActivity() {
    /** Set when opened from an alert notification; the UI navigates to Alerts and clears it. */
    private val openAlerts = mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        openAlerts.value = intent.isAlertTap()
        val app = application as XpensimateApp
        setContent { XpensimateTheme { AppRoot(app, openAlerts) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.isAlertTap()) openAlerts.value = true
    }
}

/** Foreground taps carry our extra; background ones carry the push's data payload ("event"). */
private fun Intent.isAlertTap(): Boolean =
    getBooleanExtra(Push.EXTRA_OPEN_ALERTS, false) || getStringExtra("event")?.startsWith("BUDGET_") == true

private object Loading

@Composable
private fun AppRoot(app: XpensimateApp, openAlerts: MutableState<Boolean>) {
    val stored by app.settings.config.collectAsState(initial = Loading)
    var editing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    when (val config = stored) {
        Loading -> LoadingView()
        null -> SetupScreen(initial = null) { app.settings.save(it) }
        is ServerConfig ->
            if (editing) {
                SetupScreen(initial = config) { app.settings.save(it); editing = false }
            } else {
                MainScaffold(app, config, openAlerts) { editing = true }
            }
    }
}

private enum class Tab(val route: String, val label: String, val icon: ImageVector) {
    Dashboard("dashboard", "Budget", Icons.Filled.Dashboard),
    Transactions("transactions", "Activity", Icons.AutoMirrored.Filled.ReceiptLong),
    Receipts("receipts", "Receipts", Icons.Filled.DocumentScanner),
    Bills("bills", "Bills", Icons.Filled.CalendarMonth),
    Settings("settings", "Settings", Icons.Filled.Settings),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainScaffold(
    app: XpensimateApp,
    config: ServerConfig,
    openAlerts: MutableState<Boolean>,
    onChangeServer: () -> Unit,
) {
    val api = app.api(config)
    val nav = rememberNavController()
    val context = LocalContext.current
    val askPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(config) {
        // Person and family budget lines are the server's to define; the icons need them.
        runCatching { api.taxonomy() }.onSuccess {
            PersonLines.kinds = it.personLines
            AppCurrency.code = it.currency
        }
        Push.register(app, api)
        // Send any Wealthsimple / Google Pay purchases captured while the server was unreachable; whatever
        // still can't be sent is left to the background retry.
        app.notificationQueue.flush(api)
        if (app.notificationQueue.size() > 0) QueueFlushWorker.schedule(app)
        if (Push.isAvailable(context) && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) askPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
    LaunchedEffect(openAlerts.value) {
        if (openAlerts.value) {
            nav.navigate("alerts") { launchSingleTop = true }
            openAlerts.value = false
        }
    }
    val entry by nav.currentBackStackEntryAsState()
    val route = entry?.destination?.route
    val detailTitle = when {
        route?.startsWith("transaction/") == true -> "Transaction"
        route?.startsWith("receipt/") == true -> "Receipt"
        route == "alerts" -> "Alerts"
        route == "budgets" -> "Budgets"
        route == "import" -> "Import statement"
        route == "review" -> "Month review"
        route == "categories" -> "Categories"
        route?.startsWith("line/") == true -> entry?.arguments?.getString("line")
        else -> null
    }
    val inDetail = detailTitle != null

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(detailTitle ?: Tab.entries.firstOrNull { it.route == route }?.label ?: "Xpensimate") },
                navigationIcon = {
                    if (inDetail) IconButton(onClick = { nav.popBackStack() }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    if (route == Tab.Dashboard.route) {
                        IconButton(onClick = { nav.navigate("review") }) {
                            Icon(Icons.Filled.Insights, contentDescription = "Month review")
                        }
                        IconButton(onClick = { nav.navigate("budgets") }) {
                            Icon(Icons.Filled.Edit, contentDescription = "Set budgets")
                        }
                        IconButton(onClick = { nav.navigate("alerts") }) {
                            Icon(Icons.Filled.Notifications, contentDescription = "Budget alerts")
                        }
                    }
                    if (route == Tab.Transactions.route) IconButton(onClick = { nav.navigate("import") }) {
                        Icon(Icons.Filled.UploadFile, contentDescription = "Import statement")
                    }
                },
            )
        },
        bottomBar = {
            if (!inDetail) NavigationBar {
                Tab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = route == tab.route,
                        onClick = {
                            nav.navigate(tab.route) {
                                popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = { Icon(tab.icon, contentDescription = null) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(nav, startDestination = Tab.Dashboard.route, modifier = Modifier.padding(padding)) {
            composable(Tab.Dashboard.route) {
                DashboardScreen(
                    rememberViewModel { DashboardViewModel(api) },
                    onOpenTransaction = { id -> nav.navigate("transaction/$id") },
                    onOpenBills = {
                        nav.navigate(Tab.Bills.route) {
                            popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                    },
                ) { line, month, purpose ->
                    nav.navigate("line/${Uri.encode(line)}/$month?purpose=${Uri.encode(purpose ?: "")}")
                }
            }
            composable("line/{line}/{month}?purpose={purpose}") { backStack ->
                val line = backStack.arguments?.getString("line").orEmpty()
                val month = YearMonth.parse(backStack.arguments?.getString("month"))
                val purpose = backStack.arguments?.getString("purpose")?.takeIf { it.isNotBlank() }
                LineDetailScreen(rememberViewModel(key = "line-$line-$month-$purpose") {
                    LineDetailViewModel(api, line, month, purpose)
                }) { id -> nav.navigate("transaction/$id") }
            }
            composable(Tab.Transactions.route) {
                TransactionsScreen(rememberViewModel { TransactionsViewModel(api) }) { id -> nav.navigate("transaction/$id") }
            }
            composable(Tab.Receipts.route) {
                ReceiptsScreen(rememberViewModel { ReceiptsViewModel(api) }) { id -> nav.navigate("receipt/$id") }
            }
            composable(Tab.Bills.route) { BillsScreen(rememberViewModel { BillsViewModel(api) }) }
            composable(Tab.Settings.route) { SettingsScreen(config, api, app.notificationQueue, onChangeServer) { nav.navigate("categories") } }
            composable("categories") { CategoriesScreen(rememberViewModel { CategoriesViewModel(api) }) }
            composable("review") {
                ReviewScreen(
                    rememberViewModel { ReviewViewModel(api) },
                    onOpenTransaction = { id -> nav.navigate("transaction/$id") },
                    onImport = { nav.navigate("import") },
                )
            }
            composable("import") { ImportScreen(rememberViewModel { ImportViewModel(api) }) }
            composable("budgets") { BudgetsScreen(rememberViewModel { BudgetsViewModel(api) }) }
            composable("alerts") {
                AlertsScreen(rememberViewModel { AlertsViewModel(api) }) { id -> nav.navigate("transaction/$id") }
            }
            composable("receipt/{id}") { backStack ->
                val id = backStack.arguments?.getString("id").orEmpty()
                ReceiptDetailScreen(
                    rememberViewModel(key = "receipt-$id") { ReceiptDetailViewModel(api, id) },
                    onOpenTransaction = { txnId -> nav.navigate("transaction/$txnId") },
                    onDeleted = { nav.popBackStack() },
                )
            }
            composable("transaction/{id}") { backStack ->
                val id = backStack.arguments?.getString("id").orEmpty()
                TransactionDetailScreen(rememberViewModel(key = id) { TransactionDetailViewModel(api, id) }) { receiptId ->
                    nav.navigate("receipt/$receiptId")
                }
            }
        }
    }
}
