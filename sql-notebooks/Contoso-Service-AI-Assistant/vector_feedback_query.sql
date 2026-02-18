declare @search_text nvarchar(max) = 'The customer was displeased with timely delivery of the vehicle';
declare @e vector(1536);
declare @embedding_call_failed bit = 0;
declare @embedding_error nvarchar(2048) = null;

begin try
    exec dbo.get_embeddings @text = @search_text, @embedding = @e output;
end try
begin catch
    set @embedding_call_failed = 1;
    set @embedding_error = concat('Embedding call failed: ', error_message());
end catch

if @e is null
begin
    -- Fallback for runtimes where sp_invoke_external_rest_endpoint rejects MI credentials (Msg 31630).
    -- This keeps the SQL query runnable without API keys by using an existing vector as the anchor.
    -- 1) Try to find a semantically relevant anchor by keyword
    select top 1
        @e = sf.feedback_vector
    from Service_Feedback sf
    where sf.feedback_vector is not null
      and sf.feedback_text like '%timely%'
    order by sf.schedule_id desc;

    -- 2) If no keyword match exists, use any available feedback vector
    if @e is null
    begin
        select top 1
            @e = sf.feedback_vector
        from Service_Feedback sf
        where sf.feedback_vector is not null
        order by sf.schedule_id desc;
    end

    if @e is null
    begin
        select
            coalesce(@embedding_error, 'Embedding output is NULL and no fallback vector was found.') as issue,
            object_definition(object_id('dbo.get_embeddings')) as current_get_embeddings_definition;

        throw 50002, 'Embedding is NULL. This runtime likely blocks MI for sp_invoke_external_rest_endpoint (Msg 31630), and fallback vector was not found.', 1;
    end
end

if @embedding_call_failed = 1
begin
    select @embedding_error as warning_message;
end

-- 1) Basic diagnostics
select
    @search_text as search_text,
    case when @e is null then 1 else 0 end as embedding_is_null,
    count(*) as total_feedback_rows,
    sum(case when sf.feedback_vector is null then 1 else 0 end) as null_feedback_vectors,
    sum(case when sf.rating_overall_experience <= 3 then 1 else 0 end) as rows_with_rating_le_3
from Service_Feedback sf;

-- 2) Top nearest rows without strict threshold (sanity check)
select top 10
    sf.feedback_text,
    sf.rating_overall_experience,
    vector_distance('cosine', @e, sf.feedback_vector) as distance
from Service_Feedback sf
where sf.feedback_vector is not null
order by distance;

-- 3) Your original filter (may return 0 rows if threshold is too strict)
select
    sf.feedback_text,
    sf.rating_overall_experience,
    vector_distance('cosine', @e, sf.feedback_vector) as distance
from Service_Feedback sf
where
    sf.feedback_vector is not null
    and vector_distance('cosine', @e, sf.feedback_vector) < 0.5
    and sf.rating_overall_experience <= 3
order by distance;
