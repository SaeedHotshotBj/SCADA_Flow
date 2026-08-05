USE scada_flow;
GO

ALTER TABLE PLC_Data
ADD StorageType VARCHAR(20);
GO