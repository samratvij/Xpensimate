package com.xpensimate.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.HelpOutline
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.CardGiftcard
import androidx.compose.material.icons.filled.Category
import androidx.compose.material.icons.filled.DirectionsCar
import androidx.compose.material.icons.filled.Elderly
import androidx.compose.material.icons.filled.FitnessCenter
import androidx.compose.material.icons.filled.Flight
import androidx.compose.material.icons.filled.Gavel
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.House
import androidx.compose.material.icons.filled.LocalGasStation
import androidx.compose.material.icons.filled.LocalGroceryStore
import androidx.compose.material.icons.filled.LocalLaundryService
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Restaurant
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.ShoppingBag
import androidx.compose.material.icons.filled.Spa
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Subscriptions
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** Icon for a category or budget line. Identity is carried by the icon and the name, never by color. */
fun iconFor(name: String): ImageVector = when (name) {
    "Grocery" -> Icons.Filled.LocalGroceryStore
    "Eating Out" -> Icons.Filled.Restaurant
    "Household" -> Icons.Filled.Home
    "Gas & Parking" -> Icons.Filled.LocalGasStation
    "Rent + Parking" -> Icons.Filled.House
    "Hydro" -> Icons.Filled.Bolt
    "Wifi + 5G" -> Icons.Filled.Wifi
    "Insurance" -> Icons.Filled.Shield
    "Laundry" -> Icons.Filled.LocalLaundryService
    "Loan" -> Icons.Filled.AccountBalance
    "Car" -> Icons.Filled.DirectionsCar
    "Entertainment" -> Icons.Filled.Movie
    "Travel" -> Icons.Filled.Flight
    "Gifting" -> Icons.Filled.CardGiftcard
    "Health & Fitness" -> Icons.Filled.FitnessCenter
    "Personal Care" -> Icons.Filled.Spa
    "Shopping" -> Icons.Filled.ShoppingBag
    "Subscriptions" -> Icons.Filled.Subscriptions
    "Government & Fees" -> Icons.Filled.Gavel
    "Transfer" -> Icons.Filled.SwapHoriz
    "One-time" -> Icons.Filled.Star
    "Misc" -> Icons.Filled.Category
    else -> when (PersonLines.kinds[name]) {
        "person" -> Icons.Filled.Person
        "abroad" -> Icons.Filled.Public
        "family" -> Icons.Filled.Elderly
        else -> Icons.AutoMirrored.Filled.HelpOutline
    }
}

/** The person and family budget lines (line -> person | abroad | family), as the server's /taxonomy lists them. */
object PersonLines {
    var kinds by mutableStateOf<Map<String, String>>(emptyMap())
}

@Composable
fun CategoryBadge(name: String, size: Dp = 40.dp) {
    Box(
        Modifier.size(size).background(MaterialTheme.colorScheme.secondaryContainer, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Icon(iconFor(name), contentDescription = null, tint = MaterialTheme.colorScheme.onSecondaryContainer,
            modifier = Modifier.size(size * 0.5f))
    }
}

/** Chart colors: one validated hue for magnitude, plus reserved status colors (always shown with an icon/label). */
object ChartColors {
    @Composable fun series(): Color = if (isSystemInDarkTheme()) Color(0xFF3987E5) else Color(0xFF2A78D6)
    @Composable fun seriesLight(): Color = if (isSystemInDarkTheme()) Color(0xFF1C5CAB) else Color(0xFFB7D3F6)
    val warning = Color(0xFFFAB219)
    val critical = Color(0xFFD03B3B)
    val good = Color(0xFF0CA30C)
}
