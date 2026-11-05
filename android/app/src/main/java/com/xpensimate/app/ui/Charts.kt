package com.xpensimate.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.xpensimate.app.data.MonthTotal
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale
import kotlin.math.max

private val CORNER = 4.dp

/** Diagonal stripes: "still expected", distinct from solid "spent" without relying on a second color. */
fun DrawScope.hatch(color: Color, left: Float, top: Float, right: Float, bottom: Float) {
    if (right <= left || bottom <= top) return
    val step = 5.dp.toPx()
    val stroke = 1.5.dp.toPx()
    clipRect(left, top, right, bottom) {
        val height = bottom - top
        var x = left - height
        while (x < right) {
            drawLine(color, Offset(x, bottom), Offset(x + height, top), strokeWidth = stroke)
            x += step
        }
    }
}

/** A bar whose top corners are rounded and whose base sits square on the baseline. */
private fun DrawScope.topRoundedBar(color: Color, left: Float, top: Float, width: Float, bottom: Float) {
    if (bottom - top <= 0f) return
    val r = minOf(CORNER.toPx(), width / 2, bottom - top)
    val path = Path().apply {
        moveTo(left, bottom)
        lineTo(left, top + r)
        quadraticTo(left, top, left + r, top)
        lineTo(left + width - r, top)
        quadraticTo(left + width, top, left + width, top + r)
        lineTo(left + width, bottom)
        close()
    }
    drawPath(path, color)
}

/**
 * Monthly spending, one bar per month, all in the same solid color. The current month's still-expected bills
 * sit on top as stripes. Tapping a bar selects that month; the caller shows its numbers above the chart.
 */
@Composable
fun MonthlyBars(months: List<MonthTotal>, selected: YearMonth, onSelect: (YearMonth) -> Unit, modifier: Modifier = Modifier) {
    val solid = ChartColors.series()
    val baseline = MaterialTheme.colorScheme.outlineVariant
    val selectedMark = MaterialTheme.colorScheme.onSurface
    val peak = max(1.0, months.maxOfOrNull { it.spent + it.expected } ?: 1.0)
    val labelFormat = DateTimeFormatter.ofPattern("MMM")
    val description = months.joinToString { "${YearMonth.parse(it.month).format(labelFormat)} ${it.spent.money()}" }

    Column(modifier) {
        Canvas(
            Modifier.fillMaxWidth().height(140.dp)
                .semantics { contentDescription = "Monthly spending: $description" }
                .pointerInput(months) {
                    detectTapGestures { offset ->
                        val slot = size.width / months.size.coerceAtLeast(1)
                        val index = (offset.x / slot).toInt().coerceIn(0, months.lastIndex)
                        onSelect(YearMonth.parse(months[index].month))
                    }
                },
        ) {
            val slot = size.width / months.size.coerceAtLeast(1)
            val barWidth = minOf(slot * 0.56f, 28.dp.toPx())
            val gap = 2.dp.toPx()
            months.forEachIndexed { i, m ->
                val left = i * slot + (slot - barWidth) / 2
                val spentTop = size.height * (1f - (m.spent / peak).toFloat())
                if (m.expected > 0) {
                    val expectedTop = size.height * (1f - ((m.spent + m.expected) / peak).toFloat())
                    hatch(solid, left, expectedTop, left + barWidth, spentTop - gap)
                }
                if (m.spent > 0) {
                    if (m.expected > 0) drawRect(solid, Offset(left, spentTop), Size(barWidth, size.height - spentTop))
                    else topRoundedBar(solid, left, spentTop, barWidth, size.height)
                }
                if (YearMonth.parse(m.month) == selected) {
                    // Selection is a mark under the bar, not a color change, so "spent" always looks the same.
                    drawRoundRect(selectedMark, Offset(left, size.height + 3.dp.toPx()), Size(barWidth, 3.dp.toPx()),
                        CornerRadius(1.5.dp.toPx()))
                }
            }
            drawLine(baseline, Offset(0f, size.height), Offset(size.width, size.height), strokeWidth = 1.dp.toPx())
        }
        Row(Modifier.fillMaxWidth().padding(top = 10.dp)) {
            months.forEach { m ->
                val ym = YearMonth.parse(m.month)
                Text(
                    ym.month.getDisplayName(TextStyle.SHORT, Locale.getDefault()),
                    modifier = Modifier.weight(1f),
                    textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = if (ym == selected) FontWeight.Bold else FontWeight.Normal,
                    color = if (ym == selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (months.any { it.expected > 0 }) {
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                LegendSwatch(solid, striped = false); Text("Spent", style = MaterialTheme.typography.labelSmall)
                Spacer(Modifier.width(8.dp))
                LegendSwatch(solid, striped = true); Text("Bills still due", style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun LegendSwatch(color: Color, striped: Boolean) =
    Canvas(Modifier.size(12.dp).clip(RoundedCornerShape(2.dp))) {
        if (striped) hatch(color, 0f, 0f, size.width, size.height) else drawRect(color)
    }

/**
 * One horizontal bar: value against the largest in the group, with an optional budget marker. Over budget
 * turns the bar critical-red; the caller shows the "over budget" label/icon (color is never alone).
 */
@Composable
fun ValueBar(value: Double, scaleMax: Double, budget: Double?, modifier: Modifier = Modifier) {
    val normal = ChartColors.series()
    val track = MaterialTheme.colorScheme.surfaceVariant
    val marker = MaterialTheme.colorScheme.onSurface
    val over = budget != null && value > budget
    Canvas(modifier.fillMaxWidth().height(10.dp)) {
        val scale = max(scaleMax, budget ?: 0.0).takeIf { it > 0 } ?: 1.0
        drawRoundRect(track, cornerRadius = CornerRadius(CORNER.toPx()))
        val w = (size.width * (value / scale)).toFloat().coerceIn(0f, size.width)
        if (w > 0) drawRoundRect(if (over) ChartColors.critical else normal, size = Size(w, size.height),
            cornerRadius = CornerRadius(CORNER.toPx()))
        budget?.let {
            val x = (size.width * (it / scale)).toFloat()
            drawLine(marker, Offset(x, -2.dp.toPx()), Offset(x, size.height + 2.dp.toPx()), strokeWidth = 2.dp.toPx())
        }
    }
}

/** Spent (solid) and still-expected (stripes) against a limit; anything past 100% caps at the end. */
@Composable
fun BudgetProgress(spent: Double, projected: Double, limit: Double, over: Boolean, warn: Boolean) {
    val base = when {
        over -> ChartColors.critical
        warn -> ChartColors.warning
        else -> ChartColors.series()
    }
    val track = MaterialTheme.colorScheme.surfaceVariant
    Canvas(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(CORNER))) {
        drawRect(track)
        val spentEnd = size.width * (spent / limit).toFloat().coerceIn(0f, 1f)
        val projectedEnd = size.width * (projected / limit).toFloat().coerceIn(0f, 1f)
        hatch(base, spentEnd, 0f, projectedEnd, size.height)
        if (spentEnd > 0) drawRect(base, size = Size(spentEnd, size.height))
    }
}
