-- MathWeaver MySQL schema v1
-- Generated from the current SQLite contract and normalized for MySQL 8.4.
-- Managed by Alembic revision 20260901_0001; do not apply this file directly.
SET NAMES utf8mb4;
SET time_zone = '+00:00';
CREATE TABLE `users` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `email` VARCHAR(320) COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `password_hash` VARCHAR(512) NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `can_teach` TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_users_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `sessions` (
  `token_hash` BINARY(32) NOT NULL,
  `user_id` BIGINT NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `education_role` VARCHAR(255) NULL,
  PRIMARY KEY (`token_hash`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `history` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `filename` VARCHAR(255) NOT NULL,
  `node_count` BIGINT NOT NULL DEFAULT 0,
  `edge_count` BIGINT NOT NULL DEFAULT 0,
  `created_at` DATETIME(6) NOT NULL,
  `source_markdown` LONGTEXT NULL,
  `latex_macros` JSON NULL,
  `source_pdf_json` JSON NULL,
  `status` VARCHAR(255) NOT NULL DEFAULT 'done',
  `stage` VARCHAR(255) NULL,
  `stage_label` VARCHAR(255) NULL,
  `stage_index` BIGINT NOT NULL DEFAULT 0,
  `total_stages` BIGINT NOT NULL DEFAULT 0,
  `stages_done_json` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `source_format` VARCHAR(255) NOT NULL DEFAULT 'markdown',
  `updated_at` DATETIME(6) NULL,
  `experimental_logic_ir` TINYINT(1) NOT NULL DEFAULT 0,
  `source_origin` VARCHAR(255) NOT NULL DEFAULT 'markdown',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `user_settings` (
  `user_id` BIGINT NOT NULL,
  `llm_api_url` VARCHAR(2048) NOT NULL DEFAULT '',
  `llm_model` VARCHAR(255) NOT NULL DEFAULT '',
  `updated_at` DATETIME(6) NOT NULL,
  `llm_api_key_ciphertext` LONGTEXT NOT NULL,
  `llm_configs_ciphertext` LONGTEXT NOT NULL,
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `proof_workspaces` (
  `user_id` BIGINT NOT NULL,
  `graph_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `node_id` BIGINT NOT NULL,
  `user_proof` LONGTEXT NOT NULL DEFAULT (''),
  `versions_json` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `ai_messages_json` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `updated_at` DATETIME(6) NOT NULL,
  `imports_json` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  PRIMARY KEY (`user_id`, `graph_id`, `node_id`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_classes` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `owner_user_id` BIGINT NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `invite_code` VARCHAR(255) NOT NULL,
  `archived_at` DATETIME(6) NULL,
  `created_at` DATETIME(6) NOT NULL,
  `student_experience` VARCHAR(255) NOT NULL DEFAULT 'classic',
  `weekly_xp_goal` BIGINT NOT NULL DEFAULT 60,
  `timezone` VARCHAR(255) NOT NULL DEFAULT 'Asia/Shanghai',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_education_classes_invite_code` (`invite_code`),
  CONSTRAINT `fk_education_classes_owner_user_id` FOREIGN KEY (`owner_user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_memberships` (
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `role` VARCHAR(255) NOT NULL,
  `joined_at` DATETIME(6) NOT NULL,
  `removed_at` DATETIME(6) NULL,
  `student_name` VARCHAR(255) NULL,
  `student_number` VARCHAR(128) COLLATE utf8mb4_0900_ai_ci NULL,
  PRIMARY KEY (`class_id`, `user_id`),
  UNIQUE KEY `ux_education_memberships_class_id_student_number` (`class_id`, `student_number`),
  CONSTRAINT `fk_education_memberships_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_memberships_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_snapshots` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `source_graph_id` VARCHAR(128) COLLATE utf8mb4_bin NULL,
  `filename` VARCHAR(255) NOT NULL,
  `node_count` BIGINT NOT NULL DEFAULT 0,
  `edge_count` BIGINT NOT NULL DEFAULT 0,
  `source_markdown` LONGTEXT NULL,
  `latex_macros_json` JSON NULL,
  `source_pdf_json` JSON NULL,
  `created_by` BIGINT NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `snapshot_type` VARCHAR(255) NOT NULL DEFAULT 'graph',
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_education_snapshots_created_by` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_education_snapshots_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_assignments` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `snapshot_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `target_node_id` BIGINT NOT NULL,
  `due_at` DATETIME(6) NULL,
  `status` VARCHAR(255) NOT NULL DEFAULT 'draft',
  `base_path_json` JSON NOT NULL,
  `summary` LONGTEXT NOT NULL DEFAULT (''),
  `version` BIGINT NOT NULL DEFAULT 1,
  `published_at` DATETIME(6) NULL,
  `created_by` BIGINT NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  `grades_published_at` DATETIME(6) NULL,
  `assignment_type` VARCHAR(255) NOT NULL DEFAULT 'graph',
  `direct_structure_version` BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_education_assignments_created_by` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_education_assignments_snapshot_id` FOREIGN KEY (`snapshot_id`) REFERENCES `education_snapshots` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_education_assignments_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_student_paths` (
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `path_json` JSON NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`assignment_id`, `user_id`),
  CONSTRAINT `fk_education_student_paths_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_student_paths_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_diagnostics` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `node_id` BIGINT NOT NULL,
  `question_json` JSON NOT NULL,
  `answer` LONGTEXT NULL,
  `result` LONGTEXT NULL,
  `summary` LONGTEXT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_education_diagnostics_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_diagnostics_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_ai_usage` (
  `user_id` BIGINT NOT NULL,
  `usage_day` DATE NOT NULL,
  `request_count` BIGINT NOT NULL DEFAULT 0,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`user_id`, `usage_day`),
  CONSTRAINT `fk_education_ai_usage_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_ai_tasks` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `task_key` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `task_kind` VARCHAR(255) NOT NULL,
  `scope` VARCHAR(255) NOT NULL,
  `status` VARCHAR(255) NOT NULL,
  `error` LONGTEXT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_education_ai_tasks_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_assessment_nodes` (
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `node_id` BIGINT NOT NULL,
  `status` VARCHAR(255) NOT NULL,
  `last_error` LONGTEXT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`assignment_id`, `node_id`),
  CONSTRAINT `fk_education_assessment_nodes_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_assessment_questions` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `node_id` BIGINT NOT NULL,
  `kind` VARCHAR(255) NOT NULL,
  `question` LONGTEXT NOT NULL,
  `focus` LONGTEXT NOT NULL,
  `expected_points_json` JSON NOT NULL,
  `sort_order` BIGINT NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  `reference_answer` LONGTEXT NOT NULL DEFAULT (''),
  `max_score` DOUBLE NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_education_assessment_questions_assignment_id_node_id_sort` (`assignment_id`, `node_id`, `sort_order`),
  CONSTRAINT `fk_education_assessment_questions_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_assessment_attempts` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `node_id` BIGINT NOT NULL,
  `status` VARCHAR(255) NOT NULL,
  `answers_json` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `started_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  `completed_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_education_assessment_attempts_assignment_id_user_id_node_` (`assignment_id`, `user_id`, `node_id`),
  CONSTRAINT `fk_education_assessment_attempts_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_assessment_attempts_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_node_progress` (
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `node_id` BIGINT NOT NULL,
  `state` VARCHAR(255) NOT NULL,
  `mastery_source` VARCHAR(255) NOT NULL DEFAULT 'self',
  `diagnostic_summary` LONGTEXT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`assignment_id`, `user_id`, `node_id`),
  CONSTRAINT `fk_education_node_progress_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_node_progress_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_node_identities` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `global_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `title` VARCHAR(255) NOT NULL DEFAULT '',
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_education_node_identities_class_id_global_id` (`class_id`, `global_id`),
  CONSTRAINT `fk_education_node_identities_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_node_occurrences` (
  `snapshot_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `node_id` BIGINT NOT NULL,
  `canonical_node_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `global_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  PRIMARY KEY (`snapshot_id`, `node_id`),
  CONSTRAINT `fk_education_node_occurrences_canonical_node_id` FOREIGN KEY (`canonical_node_id`) REFERENCES `education_node_identities` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_node_occurrences_snapshot_id` FOREIGN KEY (`snapshot_id`) REFERENCES `education_snapshots` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `learning_interactions` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `client_interaction_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `snapshot_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `canonical_node_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `node_id` BIGINT NOT NULL,
  `action` VARCHAR(255) NOT NULL,
  `user_proof` LONGTEXT NOT NULL,
  `assistant_response` LONGTEXT NOT NULL,
  `context_version` BIGINT NOT NULL,
  `context_snapshot_json` JSON NOT NULL,
  `classification_status` VARCHAR(255) NOT NULL,
  `token_estimate` BIGINT NOT NULL DEFAULT 0,
  `result_json` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_learning_interactions_user_id_assignment_id_client_intera` (`user_id`, `assignment_id`, `client_interaction_id`),
  CONSTRAINT `fk_learning_interactions_canonical_node_id` FOREIGN KEY (`canonical_node_id`) REFERENCES `education_node_identities` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_interactions_snapshot_id` FOREIGN KEY (`snapshot_id`) REFERENCES `education_snapshots` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_interactions_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_interactions_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_interactions_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `learning_evidence` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `interaction_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `canonical_node_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `kind` VARCHAR(255) NOT NULL,
  `claim` LONGTEXT NOT NULL,
  `status` VARCHAR(255) NOT NULL,
  `source_type` VARCHAR(255) NOT NULL,
  `confidence` DOUBLE NOT NULL,
  `severity` VARCHAR(255) NOT NULL,
  `evidence_excerpt` LONGTEXT NOT NULL DEFAULT (''),
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_learning_evidence_canonical_node_id` FOREIGN KEY (`canonical_node_id`) REFERENCES `education_node_identities` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_evidence_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_evidence_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_evidence_interaction_id` FOREIGN KEY (`interaction_id`) REFERENCES `learning_interactions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `learning_evidence_nodes` (
  `evidence_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `canonical_node_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `relation_role` VARCHAR(255) NOT NULL,
  `relation_path_json` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `weight` DOUBLE NOT NULL,
  PRIMARY KEY (`evidence_id`, `canonical_node_id`),
  CONSTRAINT `fk_learning_evidence_nodes_canonical_node_id` FOREIGN KEY (`canonical_node_id`) REFERENCES `education_node_identities` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_evidence_nodes_evidence_id` FOREIGN KEY (`evidence_id`) REFERENCES `learning_evidence` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `learning_evidence_feedback` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `evidence_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `action` VARCHAR(255) NOT NULL,
  `previous_status` VARCHAR(255) NOT NULL,
  `new_status` VARCHAR(255) NOT NULL,
  `note` VARCHAR(255) NOT NULL DEFAULT '',
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_learning_evidence_feedback_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_evidence_feedback_evidence_id` FOREIGN KEY (`evidence_id`) REFERENCES `learning_evidence` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `student_node_models` (
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `canonical_node_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `mastery_state` VARCHAR(255) NOT NULL,
  `direct_summary_json` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `risk_summary_json` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `open_evidence_count` BIGINT NOT NULL DEFAULT 0,
  `version` BIGINT NOT NULL DEFAULT 1,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`class_id`, `user_id`, `canonical_node_id`),
  CONSTRAINT `fk_student_node_models_canonical_node_id` FOREIGN KEY (`canonical_node_id`) REFERENCES `education_node_identities` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_student_node_models_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_student_node_models_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `learning_context_summaries` (
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `scope_type` VARCHAR(255) NOT NULL,
  `scope_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `summary_json` JSON NOT NULL,
  `source_watermark` VARCHAR(255) NOT NULL,
  `schema_version` BIGINT NOT NULL,
  `prompt_version` VARCHAR(255) NOT NULL,
  `token_count` BIGINT NOT NULL DEFAULT 0,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`class_id`, `user_id`, `scope_type`, `scope_id`),
  CONSTRAINT `fk_learning_context_summaries_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_learning_context_summaries_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_assignment_submissions` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `status` VARCHAR(255) NOT NULL,
  `ai_status` VARCHAR(255) NOT NULL DEFAULT 'not_started',
  `snapshot_json` JSON NOT NULL,
  `ai_suggested_total` DOUBLE NULL,
  `teacher_total` DOUBLE NULL,
  `teacher_summary` LONGTEXT NOT NULL DEFAULT (''),
  `ai_error` VARCHAR(255) NULL,
  `submitted_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  `finalized_at` DATETIME(6) NULL,
  `released_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_education_assignment_submissions_assignment_id_user_id` (`assignment_id`, `user_id`),
  CONSTRAINT `fk_education_assignment_submissions_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_assignment_submissions_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_submission_question_grades` (
  `submission_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `question_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `node_id` BIGINT NOT NULL,
  `max_score` DOUBLE NOT NULL,
  `student_answer` LONGTEXT NOT NULL,
  `reference_answer` LONGTEXT NOT NULL,
  `expected_points_json` JSON NOT NULL,
  `matrix_report_json` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `ai_result_json` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `ai_suggested_score` DOUBLE NULL,
  `teacher_score` DOUBLE NULL,
  `teacher_feedback` LONGTEXT NOT NULL DEFAULT (''),
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`submission_id`, `question_id`),
  CONSTRAINT `fk_education_submission_question_grades_submission_id` FOREIGN KEY (`submission_id`) REFERENCES `education_assignment_submissions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_assignment_sources` (
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `filename` VARCHAR(255) NOT NULL,
  `mime_type` VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream',
  `source_origin` VARCHAR(255) NOT NULL DEFAULT 'document',
  `storage_name` VARCHAR(255) NULL,
  `source_text` LONGTEXT NOT NULL DEFAULT (''),
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`assignment_id`),
  CONSTRAINT `fk_education_assignment_sources_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_game_events` (
  `id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `assignment_id` VARCHAR(128) COLLATE utf8mb4_bin NULL,
  `stage_key` VARCHAR(255) COLLATE utf8mb4_bin NULL,
  `event_type` VARCHAR(255) NOT NULL,
  `event_key` VARCHAR(255) NOT NULL,
  `base_event_key` VARCHAR(255) COLLATE utf8mb4_bin NULL,
  `xp_delta` BIGINT NOT NULL,
  `occurred_at` DATETIME(6) NOT NULL,
  `metadata_json` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_education_game_events_event_key` (`event_key`),
  CONSTRAINT `fk_education_game_events_assignment_id` FOREIGN KEY (`assignment_id`) REFERENCES `education_assignments` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_game_events_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_game_events_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_game_events_membership` FOREIGN KEY (`class_id`, `user_id`) REFERENCES `education_memberships` (`class_id`, `user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `education_student_achievements` (
  `class_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `user_id` BIGINT NOT NULL,
  `achievement_key` VARCHAR(255) NOT NULL,
  `source_event_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `unlocked_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`class_id`, `user_id`, `achievement_key`),
  CONSTRAINT `fk_education_student_achievements_source_event_id` FOREIGN KEY (`source_event_id`) REFERENCES `education_game_events` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_student_achievements_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_student_achievements_class_id` FOREIGN KEY (`class_id`) REFERENCES `education_classes` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_education_student_achievements_membership` FOREIGN KEY (`class_id`, `user_id`) REFERENCES `education_memberships` (`class_id`, `user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE `sessions` ADD CONSTRAINT `fk_sessions_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

ALTER TABLE `history` ADD CONSTRAINT `fk_history_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

ALTER TABLE `user_settings` ADD CONSTRAINT `fk_user_settings_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

ALTER TABLE `proof_workspaces` ADD CONSTRAINT `fk_proof_workspaces_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

CREATE TABLE `graph_registry` (
  `graph_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
  `graph_kind` VARCHAR(32) NOT NULL,
  `storage_status` VARCHAR(16) NOT NULL,
  `revision` BIGINT NOT NULL DEFAULT 0,
  `node_count` BIGINT NOT NULL DEFAULT 0,
  `edge_count` BIGINT NOT NULL DEFAULT 0,
  `content_sha256` CHAR(64) NULL,
  `staging_path` LONGTEXT NULL,
  `last_error` LONGTEXT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`graph_id`),
  KEY `idx_graph_registry_status` (`storage_status`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX `idx_edu_members_user` ON `education_memberships` (`user_id`);

CREATE INDEX `idx_edu_snapshots_class` ON `education_snapshots` (`class_id`, `created_at`);

CREATE INDEX `idx_edu_assignments_class` ON `education_assignments` (`class_id`, `updated_at`);

CREATE INDEX `idx_edu_diagnostics_assignment` ON `education_diagnostics` (`assignment_id`, `user_id`, `node_id`);

CREATE INDEX `idx_edu_ai_tasks_user` ON `education_ai_tasks` (`user_id`, `created_at`);

CREATE INDEX `idx_edu_assessment_questions_assignment` ON `education_assessment_questions` (`assignment_id`, `node_id`, `sort_order`);

CREATE INDEX `idx_edu_assessment_attempts_assignment` ON `education_assessment_attempts` (`assignment_id`, `user_id`, `node_id`);

CREATE INDEX `idx_edu_progress_assignment` ON `education_node_progress` (`assignment_id`, `user_id`);

CREATE INDEX `idx_edu_node_occurrences_identity` ON `education_node_occurrences` (`canonical_node_id`);

CREATE INDEX `idx_learning_interactions_node` ON `learning_interactions` (`class_id`, `user_id`, `canonical_node_id`, `created_at`);

CREATE INDEX `idx_learning_interactions_course` ON `learning_interactions` (`class_id`, `user_id`, `created_at`);

CREATE INDEX `idx_learning_evidence_course` ON `learning_evidence` (`class_id`, `user_id`, `status`, `updated_at`);

CREATE INDEX `idx_learning_evidence_nodes_node` ON `learning_evidence_nodes` (`canonical_node_id`, `relation_role`, `weight`);

CREATE INDEX `idx_student_node_models_course` ON `student_node_models` (`class_id`, `user_id`, `updated_at`);

CREATE INDEX `idx_edu_submissions_assignment` ON `education_assignment_submissions` (`assignment_id`, `status`, `user_id`);

CREATE INDEX `idx_edu_submission_grades_submission` ON `education_submission_question_grades` (`submission_id`, `node_id`);

CREATE INDEX `idx_edu_game_events_assignment` ON `education_game_events` (`assignment_id`, `user_id`);

CREATE INDEX `idx_edu_game_events_user` ON `education_game_events` (`class_id`, `user_id`, `occurred_at`);

CREATE INDEX `idx_edu_student_achievements_user` ON `education_student_achievements` (`class_id`, `user_id`, `unlocked_at`);

-- Course XP growth, gems, and course-local economy.
        CREATE TABLE education_game_mode_periods (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          mode VARCHAR(32) NOT NULL,
          starts_on DATE NOT NULL,
          ends_on DATE NULL,
          created_at DATETIME(6) NOT NULL,
          PRIMARY KEY (id),
          UNIQUE KEY ux_education_game_mode_periods_start (class_id, starts_on),
          CONSTRAINT fk_education_game_mode_periods_class FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_checkins (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          checkin_date DATE NOT NULL,
          checkin_kind VARCHAR(32) NOT NULL,
          xp_event_id VARCHAR(128) COLLATE utf8mb4_bin NULL,
          checked_in_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, checkin_date),
          CONSTRAINT fk_education_checkins_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_checkins_event FOREIGN KEY (xp_event_id) REFERENCES education_game_events (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_chest_openings (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          chest_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          chest_type VARCHAR(64) NOT NULL,
          source_ref VARCHAR(255) COLLATE utf8mb4_bin NULL,
          outcome_json JSON NOT NULL,
          opened_at DATETIME(6) NOT NULL,
          seen_at DATETIME(6) NULL,
          PRIMARY KEY (id),
          UNIQUE KEY ux_education_chest_openings_key (chest_key),
          KEY idx_education_chest_openings_user (class_id, user_id, seen_at),
          CONSTRAINT fk_education_chest_openings_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_student_wallets (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          gem_balance BIGINT NOT NULL DEFAULT 0,
          lifetime_gems_earned BIGINT NOT NULL DEFAULT 0,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id),
          CONSTRAINT fk_education_student_wallets_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_gem_ledger (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          event_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          delta BIGINT NOT NULL,
          balance_after BIGINT NOT NULL,
          source_type VARCHAR(64) NOT NULL,
          metadata_json JSON NOT NULL DEFAULT (JSON_OBJECT()),
          created_at DATETIME(6) NOT NULL,
          PRIMARY KEY (id),
          UNIQUE KEY ux_education_gem_ledger_event (event_key),
          KEY idx_education_gem_ledger_user (class_id, user_id, created_at),
          CONSTRAINT fk_education_gem_ledger_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_student_inventory (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          item_key VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
          quantity BIGINT NOT NULL DEFAULT 0,
          active_quantity BIGINT NOT NULL DEFAULT 0,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, item_key),
          CONSTRAINT fk_education_student_inventory_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_shop_items (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          item_kind VARCHAR(32) NOT NULL,
          title VARCHAR(160) NOT NULL,
          description TEXT NOT NULL,
          gem_price BIGINT NOT NULL,
          stock_quantity BIGINT NULL,
          is_active TINYINT(1) NOT NULL DEFAULT 1,
          created_by BIGINT NOT NULL,
          created_at DATETIME(6) NOT NULL,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (id),
          KEY idx_education_shop_items_class (class_id, is_active, created_at),
          CONSTRAINT fk_education_shop_items_class FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE,
          CONSTRAINT fk_education_shop_items_creator FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_shop_redemptions (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          item_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          gem_cost BIGINT NOT NULL,
          status VARCHAR(32) NOT NULL,
          item_snapshot_json JSON NOT NULL,
          fulfilled_by BIGINT NULL,
          fulfilled_at DATETIME(6) NULL,
          cancelled_at DATETIME(6) NULL,
          created_at DATETIME(6) NOT NULL,
          PRIMARY KEY (id),
          KEY idx_education_shop_redemptions_class (class_id, status, created_at),
          KEY idx_education_shop_redemptions_user (class_id, user_id, created_at),
          CONSTRAINT fk_education_shop_redemptions_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_shop_redemptions_item FOREIGN KEY (item_id) REFERENCES education_shop_items (id) ON DELETE RESTRICT,
          CONSTRAINT fk_education_shop_redemptions_fulfiller FOREIGN KEY (fulfilled_by) REFERENCES users (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_growth_rewards (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          reward_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          reward_type VARCHAR(64) NOT NULL,
          level_value BIGINT NULL,
          stage_key VARCHAR(255) COLLATE utf8mb4_bin NULL,
          payload_json JSON NOT NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'pending',
          source_event_id VARCHAR(128) COLLATE utf8mb4_bin NULL,
          created_at DATETIME(6) NOT NULL,
          claimed_at DATETIME(6) NULL,
          seen_at DATETIME(6) NULL,
          PRIMARY KEY (id),
          UNIQUE KEY ux_education_growth_rewards_key (class_id, user_id, reward_key),
          KEY idx_education_growth_rewards_user (class_id, user_id, status, created_at),
          CONSTRAINT fk_education_growth_rewards_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_growth_rewards_event FOREIGN KEY (source_event_id) REFERENCES education_game_events (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_student_collectibles (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          collectible_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          collectible_type VARCHAR(64) NOT NULL,
          title VARCHAR(160) NOT NULL,
          metadata_json JSON NOT NULL,
          equipped TINYINT(1) NOT NULL DEFAULT 0,
          unlocked_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, collectible_key),
          KEY idx_education_student_collectibles_equipped (class_id, user_id, collectible_type, equipped),
          CONSTRAINT fk_education_student_collectibles_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_weekly_goal_awards (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          week_start DATE NOT NULL,
          goal_xp BIGINT NOT NULL,
          first_event_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          completed_at DATETIME(6) NULL,
          reward_id VARCHAR(128) COLLATE utf8mb4_bin NULL,
          PRIMARY KEY (class_id, user_id, week_start),
          KEY idx_education_weekly_goal_awards_class (class_id, week_start, completed_at),
          KEY idx_education_weekly_goal_awards_reward (reward_id),
          CONSTRAINT fk_education_weekly_goal_awards_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_weekly_goal_awards_event FOREIGN KEY (first_event_id) REFERENCES education_game_events (id) ON DELETE RESTRICT,
          CONSTRAINT fk_education_weekly_goal_awards_reward FOREIGN KEY (reward_id) REFERENCES education_growth_rewards (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_class_xp_profiles (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          level_value BIGINT NOT NULL DEFAULT 1,
          level_xp BIGINT NOT NULL DEFAULT 0,
          level_goal BIGINT NOT NULL,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id),
          CONSTRAINT fk_education_class_xp_profiles_class FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_class_xp_contributions (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          week_start DATE NOT NULL,
          xp_delta BIGINT NOT NULL,
          award_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          created_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, week_start),
          CONSTRAINT fk_education_class_xp_contributions_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_class_xp_contributions_award FOREIGN KEY (award_id) REFERENCES education_growth_rewards (id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_student_stage_progress (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          stage_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          goal_xp BIGINT NOT NULL,
          current_xp BIGINT NOT NULL DEFAULT 0,
          milestone_mask BIGINT NOT NULL DEFAULT 0,
          started_at DATETIME(6) NOT NULL,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, stage_key),
          CONSTRAINT fk_education_student_stage_progress_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

        CREATE TABLE education_challenge_unlock_rules (
          assignment_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          required_level BIGINT NULL,
          required_stage_key VARCHAR(255) COLLATE utf8mb4_bin NULL,
          required_stage_milestone BIGINT NULL,
          created_at DATETIME(6) NOT NULL,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (assignment_id),
          KEY idx_education_challenge_unlock_rules_class (class_id, required_level),
          CONSTRAINT fk_education_challenge_unlock_rules_assignment FOREIGN KEY (assignment_id) REFERENCES education_assignments (id) ON DELETE CASCADE,
          CONSTRAINT fk_education_challenge_unlock_rules_class FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX `idx_edu_game_events_stage` ON `education_game_events` (`class_id`, `user_id`, `stage_key`, `occurred_at`);
