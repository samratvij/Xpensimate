package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import retrofit2.HttpException
import java.io.IOException
import java.text.DecimalFormat
import java.text.NumberFormat
import java.time.Instant
import java.time.OffsetDateTime
import java.time.YearMonth
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Currency
import java.util.Locale

sealed interface Load<out T> {
    data object Loading : Load<Nothing>
    data class Ok<T>(val value: T) : Load<T>
    data class Failed(val message: String) : Load<Nothing>
}

fun Throwable.friendly(): String = when (this) {
    is HttpException -> when (code()) {
        401 -> "The server rejected the API token."
        else -> "Server error ${code()}."
    }
    is IOException -> "Can't reach the server. Is Tailscale on?"
    else -> message ?: "Something went wrong."
}

@Composable
inline fun <reified VM : ViewModel> rememberViewModel(key: String? = null, crossinline create: () -> VM): VM =
    viewModel(key = key, factory = viewModelFactory { initializer { create() } })

/** The household's currency, from the server's /taxonomy. It's Compose state, so amounts redraw when it arrives. */
object AppCurrency {
    var code by mutableStateOf("CAD")
}

private val moneyFormats = mutableMapOf<String, NumberFormat>()

/** The phone's number style with the household's currency; "CA$", "US$" or "A$" show as plain "$". */
private fun moneyFormat(code: String): NumberFormat = moneyFormats.getOrPut(code) {
    NumberFormat.getCurrencyInstance(Locale.getDefault()).apply {
        runCatching { currency = Currency.getInstance(code) }
        (this as? DecimalFormat)?.let { format ->
            val symbols = format.decimalFormatSymbols
            if (symbols.currencySymbol.length > 1 && symbols.currencySymbol.endsWith("$")) {
                symbols.currencySymbol = "$"
                format.decimalFormatSymbols = symbols
            }
        }
    }
}

fun Double.money(): String = moneyFormat(AppCurrency.code).format(this)

val zone: ZoneId = ZoneId.systemDefault()
private val monthTitle = DateTimeFormatter.ofPattern("MMMM yyyy")
fun YearMonth.title(): String = format(monthTitle)
fun YearMonth.startUtc(): String = atDay(1).atStartOfDay(zone).toInstant().toString()
fun YearMonth.endUtc(): String = plusMonths(1).atDay(1).atStartOfDay(zone).toInstant().toString()

fun parseInstant(iso: String): Instant = OffsetDateTime.parse(iso).toInstant()

@Composable
fun MonthBar(month: YearMonth, onChange: (YearMonth) -> Unit, modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = { onChange(month.minusMonths(1)) }) {
            Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, contentDescription = "Previous month")
        }
        Text(month.title(), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f),
            textAlign = TextAlign.Center)
        IconButton(onClick = { onChange(month.plusMonths(1)) }, enabled = month < YearMonth.now()) {
            Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = "Next month")
        }
    }
}

@Composable
fun Centered(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) { content() }
}

@Composable
fun LoadingView() = Centered { CircularProgressIndicator() }

@Composable
fun ErrorView(message: String, onRetry: () -> Unit) = Centered {
    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(message, textAlign = TextAlign.Center)
        Button(onClick = onRetry) { Text("Retry") }
    }
}
