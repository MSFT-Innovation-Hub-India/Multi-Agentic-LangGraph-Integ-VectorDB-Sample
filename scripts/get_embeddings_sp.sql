/****** Object:  StoredProcedure [dbo].[get_embeddings]    Script Date: 1/13/2025 1:50:05 PM ******/

/********** 
set the following in in the script below befor creating the stored procedure  
- azure open ai endpoint name
- the ada endpoint name 
- the azure open ai api key
******************/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
ALTER procedure [dbo].[get_embeddings]
(
    @text nvarchar(max),
    @embedding vector(1536) output
)
as
begin
    declare @retval int, @response nvarchar(max);
    declare @url varchar(max);
    declare @payload nvarchar(max) = json_object('input': @text);

    -- Set the @url variable with proper concatenation before the exec statement
    set @url = 'https://<your-az-openai-endpoint>.openai.azure.com/openai/deployments/<your-ada-endpoint>/embeddings?api-version=2023-05-15';

    exec dbo.sp_invoke_external_rest_endpoint 
        @url = @url,
        @method = 'POST',   
        @payload = @payload,   
        @headers = '{"Content-Type":"application/json", "api-key":"<your azure open ai key>"}', 
        @response = @response output;

    -- Use json_query to extract the embedding array directly
    declare @jsonArray nvarchar(max) = json_query(@response, '$.result.data[0].embedding');

    -- Convert JSON array to vector and return it
    set @embedding = cast(@jsonArray as vector(1536));
end
