package com.shinyoohag.deepfakeclassifier

import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Article
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.ImageSearch
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class InputMode(val label: String) {
    TEXT("글"),
    IMAGE("사진"),
    FOLDER("폴더")
}

private object LensColors {
    val Background = Color(0xFFF7FAFC)
    val Surface = Color(0xFFFFFFFF)
    val Border = Color(0xFFD5DEE8)
    val Ink = Color(0xFF16202A)
    val Muted = Color(0xFF566575)
    val Accent = Color(0xFF116D6E)
    val AccentSoft = Color(0xFFE0F2F1)
    val Good = Color(0xFF2F855A)
    val Unknown = Color(0xFF637381)
    val Warning = Color(0xFFB7791F)
    val Danger = Color(0xFFB83232)
}

private val LensColorScheme = lightColorScheme(
    primary = LensColors.Accent,
    secondary = LensColors.Warning,
    background = LensColors.Background,
    surface = LensColors.Surface,
    error = LensColors.Danger,
    onPrimary = Color.White,
    onSecondary = Color.White,
    onBackground = LensColors.Ink,
    onSurface = LensColors.Ink
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = LensColorScheme) {
                DeepfakeLensApp()
            }
        }
    }
}

@Composable
private fun DeepfakeLensApp() {
    var mode by remember { mutableStateOf(InputMode.TEXT) }
    val scrollState = rememberScrollState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(LensColors.Background)
            .verticalScroll(scrollState)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp)
    ) {
        Header()
        ModeSwitch(mode = mode, onModeChange = { mode = it })
        when (mode) {
            InputMode.TEXT -> TextAnalysisPanel()
            InputMode.IMAGE -> ImageAnalysisPanel()
            InputMode.FOLDER -> FolderAnalysisPanel()
        }
        LimitNotice()
    }
}

@Composable
private fun Header() {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            text = "Deepfake Lens",
            style = MaterialTheme.typography.headlineLarge,
            color = LensColors.Ink,
            fontWeight = FontWeight.Bold
        )
        Text(
            text = "AI가 만든 글과 사진의 의심 신호를 로컬에서 빠르게 점검합니다.",
            style = MaterialTheme.typography.bodyLarge,
            color = LensColors.Muted
        )
    }
}

@Composable
private fun ModeSwitch(mode: InputMode, onModeChange: (InputMode) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(
            selected = mode == InputMode.TEXT,
            onClick = { onModeChange(InputMode.TEXT) },
            label = { Text("글 분석") },
            leadingIcon = {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.Article,
                    contentDescription = null,
                    modifier = Modifier.size(18.dp)
                )
            }
        )
        FilterChip(
            selected = mode == InputMode.IMAGE,
            onClick = { onModeChange(InputMode.IMAGE) },
            label = { Text("사진 분석") },
            leadingIcon = {
                Icon(
                    imageVector = Icons.Default.ImageSearch,
                    contentDescription = null,
                    modifier = Modifier.size(18.dp)
                )
            }
        )
        FilterChip(
            selected = mode == InputMode.FOLDER,
            onClick = { onModeChange(InputMode.FOLDER) },
            label = { Text("폴더 검사") },
            leadingIcon = {
                Icon(
                    imageVector = Icons.Default.FolderOpen,
                    contentDescription = null,
                    modifier = Modifier.size(18.dp)
                )
            }
        )
    }
}

@Composable
private fun TextAnalysisPanel() {
    var text by remember {
        mutableStateOf(
            "요약하자면, 이 기술은 다양한 관점에서 중요한 의미를 갖습니다. " +
                "첫째, 사용자는 더 빠르게 정보를 확인할 수 있습니다. " +
                "둘째, 균형 잡힌 접근을 통해 신뢰도를 높일 수 있습니다."
        )
    }
    var result by remember { mutableStateOf(DeepfakeClassifier.analyzeText(text)) }

    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        OutlinedTextField(
            value = text,
            onValueChange = { text = it },
            modifier = Modifier.fillMaxWidth(),
            minLines = 7,
            label = { Text("분석할 글") },
            placeholder = { Text("게시글, 댓글, 기사 문단 등을 붙여넣으세요.") }
        )
        Button(
            onClick = { result = DeepfakeClassifier.analyzeText(text) },
            colors = ButtonDefaults.buttonColors(containerColor = LensColors.Accent)
        ) {
            Icon(Icons.Default.Search, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.size(8.dp))
            Text("글 분석")
        }
        ResultPanel(result = result)
    }
}

@Composable
private fun ImageAnalysisPanel() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var selectedUri by remember { mutableStateOf<Uri?>(null) }
    var preview by remember { mutableStateOf<Bitmap?>(null) }
    var loading by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<ClassificationResult?>(null) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            selectedUri = uri
            loading = true
            scope.launch {
                val payload = withContext(Dispatchers.IO) {
                    loadImageAnalysisPayload(context, uri, includePreview = true)
                }
                preview = payload.preview
                result = payload.result
                loading = false
            }
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Button(
            onClick = { launcher.launch("image/*") },
            colors = ButtonDefaults.buttonColors(containerColor = LensColors.Accent)
        ) {
            Icon(Icons.Default.PhotoLibrary, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.size(8.dp))
            Text("사진 선택")
        }

        if (preview != null) {
            Image(
                bitmap = preview!!.asImageBitmap(),
                contentDescription = "선택한 사진",
                modifier = Modifier
                    .fillMaxWidth()
                    .height(260.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .border(1.dp, LensColors.Border, RoundedCornerShape(8.dp)),
                contentScale = ContentScale.Crop
            )
        } else {
            EmptyImageState()
        }

        if (loading) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            Text("사진을 읽고 메타데이터와 픽셀 신호를 계산하는 중입니다.", color = LensColors.Muted)
        }

        if (selectedUri != null && result != null) {
            ResultPanel(result = result!!)
        }
    }
}

@Composable
private fun FolderAnalysisPanel() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var scanning by remember { mutableStateOf(false) }
    var items by remember { mutableStateOf<List<BatchScanItem>>(emptyList()) }
    var selectedItem by remember { mutableStateOf<BatchScanItem?>(null) }
    var showCollapsed by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("지원 파일만 직접 자식 기준으로 최대 $MAX_FOLDER_SCAN_FILES 개까지 검사합니다.") }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            scanning = true
            selectedItem = null
            showCollapsed = false
            message = "폴더를 읽는 중입니다."
            scope.launch {
                val scanned = withContext(Dispatchers.IO) { scanFolder(context, uri) }
                items = scanned
                message = "스캔 완료: ${scanned.size}개 항목을 확인했습니다."
                scanning = false
            }
        }
    }

    val sortedItems = BatchScan.sort(items)
    val visibleItems = if (showCollapsed) sortedItems else sortedItems.filterNot { shouldCollapseInFolderList(it) }
    val hiddenCount = sortedItems.size - visibleItems.size

    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Button(
            onClick = { launcher.launch(null) },
            colors = ButtonDefaults.buttonColors(containerColor = LensColors.Accent)
        ) {
            Icon(Icons.Default.FolderOpen, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.size(8.dp))
            Text("폴더 선택")
        }

        Text(message, color = LensColors.Muted)

        if (scanning) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            Text("파일을 하나씩 읽고 후보를 정렬하는 중입니다.", color = LensColors.Muted)
        }

        if (items.isNotEmpty()) {
            FolderSummaryPanel(summary = BatchScan.summarize(items))
            if (hiddenCount > 0) {
                OutlinedButton(onClick = { showCollapsed = !showCollapsed }) {
                    Text(if (showCollapsed) "낮은/미지원 항목 접기" else "낮은/미지원 항목 $hiddenCount 개 보기")
                }
            }
            if (visibleItems.isEmpty()) {
                Text("우선 검토할 후보가 없습니다.", color = LensColors.Muted)
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    visibleItems.forEach { item ->
                        FolderResultRow(
                            item = item,
                            selected = item == selectedItem,
                            onClick = { selectedItem = item }
                        )
                    }
                }
            }
            selectedItem?.let { FolderDetailPanel(it) }
        }
    }
}

@Composable
private fun EmptyImageState() {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(220.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(
                Brush.linearGradient(
                    listOf(Color(0xFFE6F3F1), Color(0xFFFFF7E6), Color(0xFFF6E8EA))
                )
            )
            .border(1.dp, LensColors.Border, RoundedCornerShape(8.dp)),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(
                imageVector = Icons.Default.ImageSearch,
                contentDescription = null,
                tint = LensColors.Accent,
                modifier = Modifier.size(42.dp)
            )
            Text("갤러리에서 사진을 선택하세요.", color = LensColors.Muted)
        }
    }
}

@Composable
private fun ResultPanel(result: ClassificationResult) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = LensColors.Surface,
        tonalElevation = 1.dp,
        shadowElevation = 1.dp
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("분류 결과", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text(result.verdict, color = LensColors.Muted)
                }
                AssistChip(
                    onClick = {},
                    label = { Text(result.band.shortLabel) },
                    leadingIcon = {
                        Icon(
                            imageVector = Icons.Default.Warning,
                            contentDescription = null,
                            modifier = Modifier.size(16.dp)
                        )
                    }
                )
            }

            LinearProgressIndicator(
                progress = { result.score / 100f },
                modifier = Modifier.fillMaxWidth(),
                color = bandColor(result.band),
                trackColor = Color(0xFFE8EEF3)
            )
            Text("의심 점수 ${result.score}/100", color = LensColors.Muted)

            SourceGuessPanel(result.sourceGuess)
            SignalList(signals = result.signals)
            NextChecks(checks = result.nextChecks)
            Limitations(limitations = result.limitations)
        }
    }
}

@Composable
private fun SourceGuessPanel(sourceGuess: SourceGuess) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("추정 도구", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        Text("${sourceGuess.label} · 신뢰도 ${sourceGuess.confidence.label}", color = LensColors.Muted)
        sourceGuess.reasons.take(2).forEach { reason ->
            Text("• $reason", color = LensColors.Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun SignalList(signals: List<EvidenceSignal>) {
    val visibleSignals = signals.take(3)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("근거 신호", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        if (visibleSignals.isEmpty()) {
            Text("강한 의심 신호가 발견되지 않았습니다.", color = LensColors.Muted)
        } else {
            visibleSignals.forEach { signal ->
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(8.dp),
                    color = Color(0xFFF8FBFD)
                ) {
                    Row(
                        modifier = Modifier.padding(12.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.Top
                    ) {
                        Text(
                            text = "+${signal.weight}",
                            color = LensColors.Accent,
                            fontWeight = FontWeight.Bold
                        )
                        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                            Text(signal.title, fontWeight = FontWeight.SemiBold, color = LensColors.Ink)
                            Text(signal.detail, color = LensColors.Muted)
                        }
                    }
                }
            }
            val hidden = signals.size - visibleSignals.size
            if (hidden > 0) {
                Text("추가 근거 $hidden 개는 접어두었습니다.", color = LensColors.Muted)
            }
        }
    }
}

@Composable
private fun NextChecks(checks: List<String>) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("다음 확인", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        checks.forEach { item ->
            Text("• $item", color = LensColors.Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun Limitations(limitations: List<String>) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("주의", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        limitations.forEach { item ->
            Text("• $item", color = LensColors.Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun FolderSummaryPanel(summary: BatchScanSummary) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = LensColors.Surface,
        tonalElevation = 1.dp,
        shadowElevation = 1.dp
    ) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("폴더 요약", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SummaryChip("전체", summary.total)
                SummaryChip("높음", summary.candidates)
                SummaryChip("주의", summary.needsReview)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SummaryChip("판단 어려움", summary.unknown)
                SummaryChip("낮음", summary.lowSignal)
                SummaryChip("미지원/실패", summary.unsupportedOrFailed)
            }
        }
    }
}

@Composable
private fun SummaryChip(label: String, count: Int) {
    AssistChip(onClick = {}, label = { Text("$label $count") })
}

@Composable
private fun FolderResultRow(item: BatchScanItem, selected: Boolean, onClick: () -> Unit) {
    val result = item.result
    val borderColor = if (selected) LensColors.Accent else LensColors.Border
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, borderColor, RoundedCornerShape(8.dp))
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(8.dp),
        color = if (selected) LensColors.AccentSoft else LensColors.Surface
    ) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(item.name, fontWeight = FontWeight.SemiBold, color = LensColors.Ink)
                    Text("${item.kind.label} · ${item.status.label}", color = LensColors.Muted)
                }
                val band = result?.band
                AssistChip(
                    onClick = {},
                    label = { Text(band?.shortLabel ?: item.status.label) }
                )
            }
            if (result != null) {
                Text(
                    text = "${result.score}/100 · ${result.sourceGuess.label}",
                    color = bandColor(result.band),
                    fontWeight = FontWeight.SemiBold
                )
                Text(
                    text = result.signals.firstOrNull()?.title ?: "강한 의심 신호 없음",
                    color = LensColors.Muted
                )
            } else if (item.errorMessage != null) {
                Text(item.errorMessage, color = LensColors.Muted)
            }
        }
    }
}

@Composable
private fun FolderDetailPanel(item: BatchScanItem) {
    val result = item.result
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = LensColors.Surface,
        tonalElevation = 1.dp,
        shadowElevation = 1.dp
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(item.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text("${item.kind.label} · ${item.status.label}", color = LensColors.Muted)
            if (result != null) {
                Text(candidateSummary(result), color = LensColors.Ink, fontWeight = FontWeight.SemiBold)
                Text("의심 점수 ${result.score}/100", color = bandColor(result.band))
                item.errorMessage?.let { Text(it, color = LensColors.Muted) }
                SourceGuessPanel(result.sourceGuess)
                SignalList(signals = result.signals)
                NextChecks(checks = result.nextChecks)
            } else {
                Text(item.errorMessage ?: "이 파일은 현재 버전에서 분석하지 않습니다.", color = LensColors.Muted)
            }
        }
    }
}

@Composable
private fun LimitNotice() {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = LensColors.AccentSoft
    ) {
        Text(
            text = "참고용 로컬 검사입니다. 메타데이터가 없으면 출처 단서 없음으로 남기며, 최종 판단은 하지 않습니다.",
            modifier = Modifier.padding(14.dp),
            color = LensColors.Ink
        )
    }
}

private fun bandColor(band: RiskBand): Color {
    return when (band) {
        RiskBand.UNKNOWN -> LensColors.Unknown
        RiskBand.LOW -> LensColors.Good
        RiskBand.MEDIUM -> LensColors.Warning
        RiskBand.HIGH -> LensColors.Danger
    }
}

private fun candidateSummary(result: ClassificationResult): String {
    return when (result.band) {
        RiskBand.HIGH -> "AI 생성물 후보로 우선 검토하세요."
        RiskBand.MEDIUM -> "추가 확인이 필요한 자료입니다."
        RiskBand.UNKNOWN -> "판단할 단서가 부족합니다."
        RiskBand.LOW -> "뚜렷한 의심 신호는 적습니다."
    }
}

private fun shouldCollapseInFolderList(item: BatchScanItem): Boolean {
    return item.status != BatchItemStatus.ANALYZED || item.result?.band == RiskBand.LOW
}
