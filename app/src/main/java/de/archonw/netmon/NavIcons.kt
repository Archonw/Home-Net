package de.archonw.netmon

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Star
import androidx.compose.ui.graphics.vector.ImageVector

/** Symbole aus material-icons-core, damit keine große Icon-Bibliothek nötig ist. */
object NavIcons {
    val home: ImageVector = Icons.Filled.Home
    val devices: ImageVector = Icons.AutoMirrored.Filled.List
    val backup: ImageVector = Icons.Filled.Star
    val settings: ImageVector = Icons.Filled.Settings
    val more: ImageVector = Icons.Filled.MoreVert
    val search: ImageVector = Icons.Filled.Search
    val close: ImageVector = Icons.Filled.Close
    val back: ImageVector = Icons.AutoMirrored.Filled.ArrowBack
    val chevron: ImageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight
    val check: ImageVector = Icons.Filled.Check
}
