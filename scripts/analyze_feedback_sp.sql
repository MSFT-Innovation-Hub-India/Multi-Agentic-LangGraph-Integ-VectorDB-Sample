/****** Object:  StoredProcedure [dbo].[AnalyzeFeedback]    Script Date: 16-12-2024 10:01:54 ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
-- =============================================
-- Author:      <Author, , Name>
-- Create Date: <Create Date, , >
-- Description: <Description, , >
-- =============================================
CREATE OR ALTER PROCEDURE [dbo].[AnalyzeFeedback]
(
	@user_query_vector_json NVARCHAR(MAX)
)
AS
BEGIN
    -- SET NOCOUNT ON added to prevent extra result sets from
    -- interfering with SELECT statements.
    SET NOCOUNT ON

	 drop table if exists #query_vectors
	create table #query_vectors
	(
		id int not null identity primary key,
		vector vector(1536) not null
	);
		insert into #query_vectors (vector)
	select 
		cast(a as vector(1536))
	from
		( values 
			(@user_query_vector_json)
		) V(a)
	;
	declare @v1 vector(1536) = (SELECT vector FROM #query_vectors WHERE id = 1)
	select 
    sf.feedback_text,    
    vector_distance('cosine', @v1, sf.feedback_vector) as distance
from
    Service_Feedback sf
where
    vector_distance('cosine', @v1, sf.feedback_vector) < 0.5
	and sf.rating_overall_experience <=3
order by
    distance


END
