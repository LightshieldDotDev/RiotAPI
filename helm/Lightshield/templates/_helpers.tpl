{{- define "airflow.fullname" -}}
{{- printf "%s-airflow" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
