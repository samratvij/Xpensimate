package com.xpensimate.app.data

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

interface Api {
    @GET("api/v1/summary")
    suspend fun summary(@Query("month") month: String, @Query("purpose") purpose: String? = null): Summary

    @GET("api/v1/savings")
    suspend fun savingsPlan(): SavingsPlan

    @PUT("api/v1/savings/target")
    suspend fun setSavingsTarget(@Body body: SavingsTargetIn): SavingsTarget

    @POST("api/v1/savings/goals")
    suspend fun createGoal(@Body body: GoalIn): Goal

    @PATCH("api/v1/savings/goals/{id}")
    suspend fun updateGoal(@Path("id") id: String, @Body body: GoalIn): Goal

    @DELETE("api/v1/savings/goals/{id}")
    suspend fun deleteGoal(@Path("id") id: String): Response<Unit>

    @GET("api/v1/balances")
    suspend fun balances(@Query("month") month: String): Balances

    @PUT("api/v1/balances/{account}")
    suspend fun setBalance(@Path("account") account: String, @Body body: BalanceIn): Response<Unit>

    @Multipart
    @POST("api/v1/balances/read")
    suspend fun readBalances(@Part file: MultipartBody.Part, @Part owner: MultipartBody.Part? = null): BalanceRead

    @POST("api/v1/balances/batch")
    suspend fun saveBalances(@Body body: BalanceBatch): Response<Unit>

    @GET("api/v1/me")
    suspend fun me(): Me

    @GET("api/v1/income")
    suspend fun income(@Query("month") month: String): IncomeMonth

    @POST("api/v1/income")
    suspend fun addIncome(@Body body: IncomeIn): Transaction

    @GET("api/v1/transactions")
    suspend fun transactions(
        @Query("start") start: String? = null,
        @Query("end") end: String? = null,
        @Query("purpose") purpose: String? = null,
        @Query("budget_line") budgetLine: String? = null,
        @Query("limit") limit: Int = 200,
    ): List<Transaction>

    @GET("api/v1/transactions/{id}")
    suspend fun transaction(@Path("id") id: String): Transaction

    @PATCH("api/v1/transactions/{id}")
    suspend fun updateTransaction(@Path("id") id: String, @Body patch: TransactionPatch): Transaction

    @PUT("api/v1/transactions/{id}/items")
    suspend fun splitTransaction(@Path("id") id: String, @Body body: SplitRequest): Transaction

    @GET("api/v1/taxonomy")
    suspend fun taxonomy(): Taxonomy

    @GET("api/v1/accounts")
    suspend fun accounts(): List<Account>

    // Receipts

    @GET("api/v1/receipts")
    suspend fun receipts(@Query("limit") limit: Int = 100): List<Receipt>

    @GET("api/v1/receipts/{id}")
    suspend fun receipt(@Path("id") id: String): Receipt

    @Multipart
    @POST("api/v1/receipts/ocr")
    suspend fun uploadReceipt(@Part file: MultipartBody.Part): Receipt

    @PATCH("api/v1/receipts/{id}/items/{itemId}")
    suspend fun updateReceiptItem(@Path("id") id: String, @Path("itemId") itemId: String, @Body patch: ItemPatch): Receipt

    @GET("api/v1/receipts/{id}/candidates")
    suspend fun candidates(@Path("id") id: String): List<MatchCandidate>

    @POST("api/v1/receipts/{id}/match")
    suspend fun matchReceipt(@Path("id") id: String, @Body body: MatchRequest): Receipt

    @POST("api/v1/receipts/{id}/unmatch")
    suspend fun unmatchReceipt(@Path("id") id: String): Receipt

    @DELETE("api/v1/receipts/{id}")
    suspend fun deleteReceipt(@Path("id") id: String): Response<Unit>

    // Recurring bills

    @GET("api/v1/recurring/expected")
    suspend fun expected(@Query("month") month: String): List<ExpectedPayment>

    @POST("api/v1/recurring/{id}/paid")
    suspend fun markPaid(@Path("id") id: String, @Body body: MarkPaid): Transaction

    @POST("api/v1/recurring")
    suspend fun createRecurring(@Body body: Map<String, String>): Recurring

    // Budgets (keyed by budget line, e.g. "Grocery" or "Alex Personal")

    @GET("api/v1/budgets")
    suspend fun budgets(): List<Budget>

    @PUT("api/v1/budgets/{line}")
    suspend fun setBudget(@Path("line") line: String, @Body body: BudgetUpdate): Budget

    @DELETE("api/v1/budgets/{line}")
    suspend fun removeBudget(@Path("line") line: String): Response<Unit>

    // Categories

    @GET("api/v1/categories")
    suspend fun categories(): List<CategoryInfo>

    @POST("api/v1/categories")
    suspend fun addCategory(@Body body: CategoryNameBody): CategoryInfo

    @POST("api/v1/categories/{name}/rename")
    suspend fun renameCategory(@Path("name") name: String, @Body body: CategoryNameBody): CategoryInfo

    @DELETE("api/v1/categories/{name}")
    suspend fun mergeCategory(@Path("name") name: String, @Query("into") into: String): Response<Unit>

    // Insights

    @GET("api/v1/trends")
    suspend fun trends(@Query("months") months: Int = 6, @Query("purpose") purpose: String? = null): List<MonthTotal>

    @GET("api/v1/review")
    suspend fun review(@Query("month") month: String): MonthReview

    @GET("api/v1/insights")
    suspend fun insights(@Query("purpose") purpose: String? = null): List<Insight>

    @POST("api/v1/insights/dismiss")
    suspend fun dismissInsight(@Body body: DismissInsight): Response<Unit>

    // Wealthsimple capture

    @POST("api/v1/notifications")
    suspend fun sendNotification(@Body body: PhoneNotification): PhoneNotificationResult

    // Statement import

    @Multipart
    @POST("api/v1/statements/preview")
    suspend fun previewStatement(
        @Part file: MultipartBody.Part,
        @Part month: MultipartBody.Part? = null,
        @Part accountId: MultipartBody.Part? = null,
    ): StatementImport

    @GET("api/v1/statements/{id}")
    suspend fun statement(@Path("id") id: String): StatementImport

    @POST("api/v1/statements/{id}/commit")
    suspend fun commitStatement(@Path("id") id: String, @Body body: StatementCommit): StatementImport

    // Push notifications

    @POST("api/v1/devices")
    suspend fun registerDevice(@Body body: DeviceRegistration): Response<Unit>

    @POST("api/v1/devices/test")
    suspend fun sendTestNotification(): PushTestResult

    @GET("api/v1/health")
    suspend fun health(): HealthReport

    @GET("api/v1/alerts")
    suspend fun alerts(@Query("limit") limit: Int = 100): List<BudgetAlert>

    // Chat assistant: answers come back whole (the web app streams them)

    @POST("api/v1/chat")
    suspend fun ask(@Body body: ChatAsk): ChatReply

    @GET("api/v1/chat/usage")
    suspend fun chatUsage(): ChatUsage

    @GET("api/v1/chat/threads")
    suspend fun chatThreads(): List<ChatThread>

    @GET("api/v1/chat/threads/{id}")
    suspend fun chatThread(@Path("id") id: String): ChatThreadDetail

    @DELETE("api/v1/chat/threads/{id}")
    suspend fun deleteChatThread(@Path("id") id: String): Response<Unit>

    @GET("api/v1/chat/memory")
    suspend fun chatMemory(): List<ChatMemory>

    @POST("api/v1/chat/memory")
    suspend fun addChatMemory(@Body body: ChatMemoryIn): ChatMemory

    @DELETE("api/v1/chat/memory/{id}")
    suspend fun deleteChatMemory(@Path("id") id: String): Response<Unit>
}

val json = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
}

fun createApi(config: ServerConfig): Api {
    val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // Categorizing waits on the AI model for seconds; reading a receipt photo can take half a minute, and a chat
        // answer that searches the web (or runs on a local model) longer.
        .readTimeout(240, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .addInterceptor { chain ->
            chain.proceed(chain.request().newBuilder().header("Authorization", "Bearer ${config.token}").build())
        }
        .build()
    return Retrofit.Builder()
        .baseUrl(config.baseUrl.trimEnd('/') + "/")
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(Api::class.java)
}
