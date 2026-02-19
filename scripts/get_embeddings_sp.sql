/****** Object:  StoredProcedure [dbo].[get_embeddings] ******/

/**********
Managed Identity only (no API key).

Prerequisites:
1) Azure SQL Server system-assigned managed identity must be enabled.
2) That identity must have role "Cognitive Services OpenAI User" on Azure OpenAI.

If this database runtime returns Msg 31630 for sp_invoke_external_rest_endpoint,
it means outbound REST in this runtime does not support MI credentials for this proc.
In that case, generate embeddings in app/service layer using MI and pass vectors to SQL.
******************/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.symmetric_keys
    WHERE name = '##MS_DatabaseMasterKey##'
)
BEGIN
    CREATE MASTER KEY ENCRYPTION BY PASSWORD = 'TempStrong#2026!RotateImmediately';
    ALTER MASTER KEY ADD ENCRYPTION BY SERVICE MASTER KEY;
END
GO

IF EXISTS (
    SELECT 1
    FROM sys.database_scoped_credentials
    WHERE name = 'OpenAICredentialMI'
)
BEGIN
    DROP DATABASE SCOPED CREDENTIAL [OpenAICredentialMI];
END
GO

IF EXISTS (
    SELECT 1
    FROM sys.database_scoped_credentials
    WHERE name = 'OpenAICredentialMI'
)
BEGIN
    DROP DATABASE SCOPED CREDENTIAL [OpenAICredentialMI];
END
GO

CREATE DATABASE SCOPED CREDENTIAL [OpenAICredentialMI]
WITH IDENTITY = 'Managed Identity';
GO

CREATE OR ALTER PROCEDURE [dbo].[get_embeddings]
(
    @text nvarchar(max),
    @embedding vector(1536) output
)
as
begin
    declare @retval int, @response nvarchar(max);
    declare @url varchar(max);
    declare @payload nvarchar(max) = json_object('input': @text);

    -- TODO: Update this URL to match YOUR Azure OpenAI endpoint and embedding deployment name.
    set @url = 'https://<your-openai>.openai.azure.com/openai/deployments/text-embedding-ada-002/embeddings?api-version=2023-05-15';

    begin try
        exec dbo.sp_invoke_external_rest_endpoint 
            @url = @url,
            @method = 'POST',   
            @payload = @payload,   
            @headers = '{"Content-Type":"application/json"}',
            @credential = [OpenAICredentialMI],
            @response = @response output;
    end try
    begin catch
        if error_number() = 31630
        begin
            throw 50003, 'Managed identity credential is policy-compliant but not supported by sp_invoke_external_rest_endpoint in this Azure SQL runtime (Msg 31630). Use app/service-layer MI to generate embedding and pass vector to SQL query.', 1;
        end
        else
        begin
            throw;
        end
    end catch

    -- Parse different response shapes returned by sp_invoke_external_rest_endpoint
    declare @jsonArray nvarchar(max) = coalesce(
        json_query(@response, '$.data[0].embedding'),
        json_query(@response, '$.result.data[0].embedding'),
        json_query(@response, '$.result.body.data[0].embedding'),
        json_query(@response, '$.response.data[0].embedding')
    );

    if @jsonArray is null
    begin
        declare @statusCode int = try_cast(coalesce(
            json_value(@response, '$.result.status.http.code'),
            json_value(@response, '$.result.status.code')
        ) as int);

        declare @errorBody nvarchar(max) = coalesce(
            json_query(@response, '$.result.body'),
            @response
        );

        declare @errMsg nvarchar(2048) =
            concat(
                'Failed to fetch embedding from Azure OpenAI. HTTP status: ',
                coalesce(cast(@statusCode as nvarchar(20)), 'unknown'),
                '. Response: ',
                left(@errorBody, 1800)
            );

        throw 50001, @errMsg, 1;
    end

    -- Convert JSON array to vector and return it
    set @embedding = cast(@jsonArray as vector(1536));
end
